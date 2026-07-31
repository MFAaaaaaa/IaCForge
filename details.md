# IaCForge v2 各模块输入、输出与真实工作流程

本文档描述附件建议落实后的真实代码，不描述尚不存在的概念功能。

## 1. 一句话结论

IaCForge v2 的准确表述是：

> IaCForge uses stage-specific task–provider grounding: a planner projection
> supports typed task-graph construction, while a compiler projection combines
> exact provider schemas and provenance-backed reference patterns into an
> instance-level generation contract.

它不再把同一份完整 KG evidence 同时交给两个 LLM 阶段，也不再把 Schema
Grounding 称为向量 RAG。

## 2. 完整流程

```text
离线：
AWS Provider 5.90.0 Schema
+ 同版本官方文档
+ 官方 HCL examples
    -> Stable Typed Provider KG
    -> Resource BM25 Index
    -> 可选 Dense Semantic Index
    -> Dependency Evidence Index
    -> Exact Schema Index
    -> 带完整 provenance 的 Prompt Cache

在线：
Visible Prompt
    -> Hybrid Resource Linking
    -> Planner Evidence Projection
    -> Planner LLM
    -> Graph IR v2 Safe Parse
    -> IRSchemaChecker
    -> Normalized Typed Graph IR
    -> IR-guided Exact Schema Grounding
    -> Graph IR + Schema + KG References
    -> Canonical Provider Contract v2
    -> Compiler LLM
    -> Common Normalize
    -> HCL Mechanism Metrics
    -> Terraform Validate
    -> Terraform Plan
    -> OPA
```

## 3. 全局版本链

对应模块：`evaluation/versioning.py`

固定版本：

```json
{
  "terraform_version": "1.9.8",
  "provider_name": "hashicorp/aws",
  "provider_version": "5.90.0",
  "graph_ir_version": "2.0",
  "contract_version": "2.0",
  "retriever_version": "hybrid-v2"
}
```

输入：

- Provider Schema 文件；
- KG 根目录及 `metadata.json`；
- 运行时 `AWS_PROVIDER_VERSION_CONSTRAINT`。

输出：

- `schema_sha256`；
- `kg_sha256`；
- 完整 version manifest。

错误行为：

- 运行 Provider 不是 5.90.0 时直接失败；
- 不允许用 5.90.0 KG/Schema 去验证 5.100.0 Provider；
- 若要升级 Provider，必须重建 Schema、Docs、KG、Dense Index 和 Cache。

## 4. Typed KG 模块

对应模块：`evaluation/iac_kg/typed_kg.py`

### 4.1 输入

- `resources.jsonl`；
- `kg_edges.jsonl`；
- Provider 版本。

### 4.2 节点输出

实体类型：

```text
ResourceType
DataSourceType
Argument
NestedBlock
ExportedAttribute
Example
ProviderVersion
Service
```

稳定 ID：

```text
aws@5.90.0::resource::aws_subnet
aws@5.90.0::resource::aws_subnet::argument::vpc_id
aws@5.90.0::resource::aws_vpc::attribute::id
```

### 4.3 边输出

关系类型：

```text
HAS_ARGUMENT
HAS_BLOCK
EXPORTS
HAS_EXAMPLE
REFERENCES
```

REFERENCES 边形式：

```json
{
  "edge_id": "ref:aws_subnet.vpc_id->aws_vpc.id",
  "source_type": "aws_subnet",
  "source_path": "vpc_id",
  "target_type": "aws_vpc",
  "target_path": "id",
  "relation": "REQUIRES_VALUE_OF_TYPE",
  "provenance": "official_doc_example",
  "source_document": "website/docs/r/subnet.html.markdown",
  "example_title": "Basic Usage",
  "support_count": 4,
  "confidence": 1.0,
  "provider_version": "5.90.0"
}
```

注意：`REQUIRES_VALUE_OF_TYPE` 只表示参数需要兼容值，并不等价于必须创建
目标 resource。目标也可以来自 data source、external input、literal 或 module
output。

### 4.4 物化命令

```bash
python3 scripts/build_typed_kg.py
```

输出：

```text
typed_nodes.jsonl
typed_edges.jsonl
```

## 5. KG 质量报告

对应接口：`typed_kg.kg_quality_report`

输入：KG 根目录。

结构化输出：

- description coverage；
- argument documentation coverage；
- parseable HCL example coverage；
- 有 REFERENCES 出边的类型比例；
- 不同 provenance 的边数；
- 文档字段无法对齐 Schema 的数量；
- Schema 字段缺少文档描述的数量；
- 双人标注所需的 200 条固定随机样本。

命令：

```bash
cd evaluation
python3 iacforge_cli.py kg-quality \
  --audit-sample ../results/reference_edge_audit.csv
```

Cohen’s κ 和人工 precision 不由代码伪造；必须由两名标注者独立完成后统计。

## 6. Prompt-to-KG Hybrid Retrieval

对应模块：`evaluation/iac_kg/provider_contract_retriever.py`

### 6.1 输入

```text
Visible Prompt
public KG resource contracts
public dependency templates
auditable alias/concept/reference rules
optional dense resource index
retrieval parameters
```

严禁输入：

```text
IaC-Eval Resource
Intent
Rego intent
Reference output
Validate/Plan/OPA feedback
历史生成 HCL
历史 repair trace
```

### 6.2 四阶段检索

```text
Stage 1  exact type / alias
Stage 2  resource-level BM25
Stage 3  optional dense semantic retrieval
Stage 4  KG/provenance-aware reranking
```

Dense Index 只编码：

- resource/data source 名称；
- aliases；
-简短 description；
- 少量用途文本。

它不编码所有 argument、computed attribute 和 nested block，避免字段级文本干扰
resource selection。

### 6.3 动态候选预算

配置：

```text
IAC_CONTRACT_MIN_RESOURCES=3
IAC_CONTRACT_MAX_RESOURCES=12
IAC_CONTRACT_SCORE_THRESHOLD=8
IAC_CONTRACT_SCORE_GAP=24
```

候选按 threshold 和相邻 score gap 提前截断。少于三个正分候选时不会用零分
资源强行补足。

### 6.4 手写规则审计

以下规则全部能转换为独立、带来源的记录：

```text
RESOURCE_ALIASES
EXTRA_RESOURCE_ALIASES
CONCEPT_BUNDLES
REFERENCE_ATTR_HINTS
```

输出示例：

```json
{
  "rule_id": "concept:nat_gateway",
  "kind": "concept_bundle",
  "terms": ["nat gateway", "elastic ip allocation"],
  "candidate_resources": ["aws_eip", "aws_nat_gateway", "aws_subnet"],
  "source": "author_defined_public_domain_knowledge"
}
```

命令：

```bash
python3 evaluation/iacforge_cli.py retrieval-rules --pretty
```

每次 cache/run 记录 `retrieval_rules_sha256`，便于做规则消融。

### 6.5 Full Evidence 输出

Full Evidence 是检索器的内部完整结果，含：

- candidate resources 及 score/matched rules；
- dependency templates 及 provenance/confidence；
- prompt semantic slots；
- resource type contracts；
- nested-block facts；
- value bindings；
- usage/negative constraints。

此完整对象不再直接注入 Planner LLM。

## 7. Planner Evidence Projection

对应模块：`evaluation/evidence_projection.py`

函数：

```python
project_planner_evidence(full_evidence) -> PlannerEvidence
```

输入：Full Evidence。

输出：

```json
{
  "planner_evidence_version": "2.0",
  "candidate_resources": [
    {
      "type": "aws_subnet",
      "purpose": "resource match reason",
      "score": 42.0,
      "matched_by": ["alias:subnet", "bm25_top30"],
      "evidence_ids": ["provider_contract_resource:aws_subnet"]
    }
  ],
  "dependency_candidates": [
    {
      "from_type": "aws_subnet",
      "to_type": "aws_vpc",
      "relation": "REQUIRES_VALUE_OF_TYPE",
      "source_field": "vpc_id",
      "target_field": "id",
      "confidence": 0.98,
      "provenance": "official_doc_example"
    }
  ],
  "alternatives": [],
  "prompt_slots": {
    "cidr_blocks": ["10.0.1.0/24"]
  }
}
```

明确排除：

- 全部 optional arguments；
- computed attributes 清单；
- 大量 nested blocks；
- 完整官方 examples；
- HCL 语法细节。

## 8. 离线 Cache

对应模块：`evaluation/iac_kg/offline_provider_contract_cache.py`

Cache entry：

```json
{
  "prompt_sha256": "...",
  "retriever_version": "hybrid-v2",
  "provider_version": "5.90.0",
  "kg_sha256": "...",
  "schema_sha256": "...",
  "retrieval_parameters": {},
  "candidate_scores": [],
  "evidence_sha256": "...",
  "evidence": {}
}
```

在线与离线等价性使用 canonical JSON SHA-256，而不是普通字符串顺序。

重建命令：

```bash
python3 scripts/build_offline_cache.py
```

非 IaC-Eval Prompt 直接测试：

```bash
cd evaluation
python3 iacforge_cli.py retrieve \
  --prompt "Use an existing VPC and create a public subnet" \
  --projection planner
```

## 9. Planner LLM

对应：

- `prompt_templates_verigraph.py`
- `models.py`

输入：

```text
Visible Prompt
+ Planner Evidence（full/planner_kg 模式）
```

输出：单个 Graph IR v2 JSON。

默认解码：

```text
temperature=0
top_p=1
max_tokens=1536
response_format=json_object
```

若本地 OpenAI-compatible server 不支持 `response_format`，客户端只删除该参数重试；
后续 safe parser 仍然执行。

## 10. Graph IR v2

对应模块：`evaluation/graph_ir.py`

### 10.1 输入

Planner LLM 原始文本。

### 10.2 标准输出

```json
{
  "graph_ir_version": "2.0",
  "resources": [
    {
      "id": "main_vpc",
      "type": "aws_vpc",
      "kind": "resource",
      "role": "main network"
    },
    {
      "id": "public_subnet",
      "type": "aws_subnet",
      "kind": "resource",
      "role": "public subnet"
    }
  ],
  "bindings": [
    {
      "id": "binding:public_subnet.vpc_id->main_vpc.id",
      "source": {
        "resource": "public_subnet",
        "path": "vpc_id"
      },
      "target": {
        "resource": "main_vpc",
        "path": "id"
      },
      "kind": "attribute_reference",
      "evidence_id": "ref:aws_subnet.vpc_id->aws_vpc.id"
    }
  ],
  "constraints": [
    {
      "id": "constraint_1",
      "target": "public_subnet.map_public_ip_on_launch",
      "operator": "equals",
      "value": true,
      "value_kind": "boolean",
      "source_text": "public subnet",
      "confidence": 0.93
    }
  ],
  "explicit_dependencies": [],
  "requirements": [
    {
      "id": "req_1",
      "text": "Create a public subnet in the VPC",
      "implemented_by": [
        "resource:public_subnet",
        "binding:public_subnet.vpc_id->main_vpc.id",
        "constraint:public_subnet.map_public_ip_on_launch=true"
      ]
    }
  ],
  "notes": []
}
```

`source` 是使用值的 consumer argument，`target` 是提供值的 producer attribute。

### 10.3 失败降级

确定性恢复顺序：

1. 搜索 Markdown fence；
2. 从每个 `{` 尝试 JSON decoder；
3. 取第一个合法 JSON object；
4. 转换旧 v1 IR；
5. 做字段类型和引用端点检查；
6. 仍失败则生成空但合法的 v2 IR；
7. 标记 `ir_generation_failure=true`。

原始错误文本只进入日志，永远不会进入 HCL Prompt。

## 11. IRSchemaChecker

对应模块：`evaluation/ir_schema_checker.py`

输入：

```text
Normalized Graph IR
AWS Provider Schema 5.90.0
```

检查项：

- type 是否存在；
- resource/data source kind 是否正确；
- binding source argument 是否存在且 assignable；
- binding target attribute 是否存在且 exported；
- source/target Schema type 是否兼容；
- 两端 instance 是否已声明。

输出：

```json
{
  "valid": false,
  "violations": [
    {
      "code": "UNKNOWN_OR_UNASSIGNABLE_SOURCE_ARGUMENT",
      "path": "bindings[0].source.path",
      "suggested_path": "vpc_id"
    }
  ],
  "normalization_actions": [
    {
      "code": "SAFE_SOURCE_PATH_CORRECTION",
      "from": "vpc",
      "to": "vpc_id",
      "binding_index": 0
    }
  ]
}
```

只修复唯一、高置信度路径，不自动新增/删除资源。

## 12. IR-guided Exact Schema Grounding

对应模块：

- `evaluation/schema_rag.py`
- `evaluation/provider_schema.py`

输入：

```text
Normalized Graph IR
Visible Prompt
Provider Schema 5.90.0
```

选择层次：

```text
Tier 1  全部 required arguments
Tier 2  IR bindings/constraints 中的字段
Tier 3  Prompt token 对应的 optional 字段
Tier 4  binding target 需要的 computed attributes
Tier 5  IR 涉及的 nested blocks
```

输出：

```json
{
  "schema_contract_version": "2.0",
  "retrieval_method": "ir_guided_exact_schema_grounding",
  "resources": [
    {
      "instance_id": "public_subnet",
      "type": "aws_subnet",
      "kind": "resource",
      "required_args": ["vpc_id"],
      "relevant_optional_args": [
        "cidr_block",
        "map_public_ip_on_launch"
      ],
      "computed_attrs": [],
      "all_computed_attrs": ["arn", "id"],
      "arg_types": {
        "vpc_id": "string",
        "cidr_block": "string",
        "map_public_ip_on_launch": "bool"
      },
      "nested_blocks": []
    }
  ],
  "missing_types": [],
  "negative_constraints": []
}
```

不存在的类型会进入 `negative_constraints`，同时记录
`hallucinated_resource_type_rate`。

## 13. Canonical Provider Contract

对应模块：`evaluation/provider_contract.py`

函数：

```python
build_provider_contract(
    prompt,
    normalized_ir,
    schema_projection,
    kg_evidence
) -> ProviderContract
```

输入：

- Visible Prompt；
- Normalized Graph IR；
- Exact Schema Projection；
- 完整 KG evidence（仅在确定性 Contract Builder 内使用）。

主要工作：

1. 按 IR instance 展开 contract；
2. 将 IR binding 转为精确 Terraform expression；
3. 只实例化高 provenance/confidence 的 KG reference；
4. schema-name hint 不作为硬 binding；
5. 映射 Prompt literal；
6. 映射结构化 constraint；
7. 加入 computed-only forbidden assignment；
8. 标出缺失 required 和 unresolved requirement。

输出：

```json
{
  "contract_version": "2.0",
  "instance_contracts": {
    "public_subnet": {
      "type": "aws_subnet",
      "kind": "resource",
      "generation_policy": "must_generate_block",
      "required_assignments": ["vpc_id"],
      "allowed_assignments": [
        "vpc_id",
        "cidr_block",
        "map_public_ip_on_launch"
      ],
      "must_assign": {
        "vpc_id": {
          "kind": "reference",
          "expression": "aws_vpc.main_vpc.id",
          "binding_id": "binding:public_subnet.vpc_id->main_vpc.id"
        }
      },
      "should_assign": {
        "map_public_ip_on_launch": {
          "kind": "literal",
          "value": true
        }
      },
      "unresolved_required_assignments": [],
      "forbidden_assignments": ["id", "arn"],
      "nested_blocks": []
    }
  },
  "bindings": [],
  "usage_constraints": [],
  "negative_constraints": [],
  "explicit_dependencies": [],
  "unresolved_constraints": [],
  "unresolved_requirements": [],
  "contract_sha256": "..."
}
```

HCL Prompt、日志和 Contract Validator 使用同一套字段，不再维护两套相似命名。

## 14. Deterministic HCL Skeleton

对应函数：`provider_contract.build_hcl_skeleton`

输入：Provider Contract。

输出：

```hcl
resource "aws_vpc" "main_vpc" {
  # REQUIRED: assign cidr_block from the visible prompt/contract
}

resource "aws_subnet" "public_subnet" {
  vpc_id = aws_vpc.main_vpc.id
  map_public_ip_on_launch = true
  # REQUIRED: assign cidr_block from the visible prompt/contract
}
```

该功能不属于默认完整流程，仅保留用于独立消融。默认值：

```text
IAC_USE_HCL_SKELETON=0
```

仅在显式设置 `IAC_USE_HCL_SKELETON=1` 时，Skeleton 才会加入 Compiler
Prompt。Skeleton 不猜未知值；启用后 LLM 只补 Prompt literal、未解析
required、相关 optional 和 nested block。

## 15. Compiler LLM

输入：

```text
Visible Prompt
Normalized Graph IR
Exact Schema Projection
Canonical Provider Contract
```

默认流程不输入 HCL Skeleton。显式设置 `IAC_USE_HCL_SKELETON=1` 时，可将其
作为单独的工程增强/消融变量加入。

强制规则：

1. 每个 IR/contract instance 恰好一个 block；
2. 不新增 contract 之外的 managed resource；
3. 实现全部 bindings；
4. 满足 required assignments；
5. 不赋值 computed-only 字段；
6. `depends_on` 只来自 `explicit_dependencies`。

默认解码：

```text
temperature=0
top_p=1
max_tokens=max(1536, 1024 + 384 * instance_count)
上限 4096
```

输出：一个 HCL candidate。

## 16. Normalize 与 HCL 机制指标

对应：

- `eval_verigraph.normalize_terraform_config_with_diff`
- `evaluation/hcl_metrics.py`

所有模式使用同一个 Normalize：

- Terraform block；
- AWS Provider 5.90.0；
- region；
- static-plan skip flags；
- 相同 HCL extraction。

日志同时保存：

```text
raw_llm_hcl
normalized_hcl
normalization_diff
```

Terraform validate 前输出：

- resource/data source 数；
- IR node realization；
- IR binding realization；
- extra resource；
- missing IR resource；
- computed-only assignment；
- unsupported assignment；
- missing required assignment。

## 17. Validate、Plan 与 OPA

输入：统一 Normalize 后的 `main.tf`。

顺序：

```text
terraform init
terraform validate
terraform plan -refresh=false
terraform show -json
opa eval data
```

OPA policy 只在最终 HCL 已经生成并成功 plan 后读取。生成模块看不到 Rego。

## 18. Local Repair

模式：

```text
full_strict
full_repair1
```

`full_repair1` 最多一次 repair，输入仅含：

```text
Visible Prompt
Normalized Graph IR
Provider Contract
原始 HCL
Terraform validate/plan diagnostic
```

明确不含：

```text
OPA policy
OPA result
OPA failure
```

日志记录 initial/final validate、plan、repair call、tokens 和 latency。

## 19. 结果日志

`LLM Notes #0` 的核心结构：

```json
{
  "version_manifest": {},
  "retrieval": {
    "candidate_types": [],
    "scores": [],
    "matched_rules": [],
    "planner_evidence_sha256": ""
  },
  "graph_ir": {
    "raw_ir": "",
    "normalized_ir": {},
    "errors": [],
    "warnings": [],
    "normalization_actions": [],
    "schema_consistency": {}
  },
  "schema_rag": {},
  "compiler_contract": {
    "contract_sha256": "",
    "resource_instances": [],
    "bindings": [],
    "validation": {}
  },
  "generation_cost": {
    "retrieval_ms": 0,
    "ir": {
      "input_tokens": 0,
      "output_tokens": 0,
      "latency_ms": 0
    },
    "hcl": {
      "input_tokens": 0,
      "output_tokens": 0,
      "latency_ms": 0
    }
  },
  "hcl_generation": {
    "raw_llm_hcl": "",
    "normalized_hcl": "",
    "normalization_diff": "",
    "mechanism_metrics": {}
  }
}
```

## 20. 实验模式

| 模式 | Planner KG | Schema | Compiler KG | Repair |
| --- | ---: | ---: | ---: | ---: |
| baseline | 否 | 否 | 否 | 否 |
| ir_only | 否 | 否 | 否 | 否 |
| ir_schema | 否 | 是 | 否 | 否 |
| planner_kg | 是 | 是 | 否 | 否 |
| compiler_kg | 否 | 是 | 是 | 否 |
| full/full_strict | 是 | 是 | 是 | 否 |
| full_repair1 | 是 | 是 | 是 | 最多一次 |

检索消融：

```text
lexical
dense
hybrid
hybrid_graph
```

边消融可以通过 provenance/source kind 过滤 official-example edge 和 name-hint edge。

## 21. Resource Retrieval 离线指标

对应：

- `evaluation/retrieval_metrics.py`
- `scripts/evaluate_retrieval.py`

Gold `Resource` 仅在候选检索完成后用于评估：

```text
Recall@1/3/5/10
Precision@1/3/5/10
MRR
candidate set size
gold resource not retrieved
```

生成阶段不读取 Gold Resource。

## 22. 运行与重建顺序

首次升级 v2：

```bash
cd /home/fameng/zzzhong/IaCForge

python3 scripts/build_typed_kg.py
python3 evaluation/iacforge_cli.py kg-quality \
  --audit-sample results/reference_edge_audit.csv

# 可选：需 embeddings endpoint
python3 scripts/build_dense_index.py

python3 scripts/build_offline_cache.py
python3 scripts/evaluate_retrieval.py
python3 -m unittest discover -s tests -v
python3 scripts/verify_package.py --strict
```

正式运行：

```bash
MODE=full MODEL=qwen2.5-coder-3b MAX_ROWS=458 \
./scripts/run_framework.sh
```

## 23. 当前实现边界

已经完成：

- P0 的版本对齐、接口统一、阶段投影、安全 IR、统一 Normalize；
- Typed Graph IR；
- IR-Schema Checker；
- Canonical Provider Contract；
- typed KG/provenance/confidence；
- conditional dependency expansion；
- exact/BM25/optional dense/hybrid retrieval；
- 动态预算、规则审计、缓存 provenance；
- 中间指标、消融模式、严格/一次 repair；
- 新单元测试。

仍需在服务器数据上执行，而不是由源码修改自动完成：

- 物化 `typed_nodes.jsonl` 和 `typed_edges.jsonl`；
- 若具备 embedding 模型，构建 `resource_dense_index.json`；
- 用 hybrid-v2 重建 458 Prompt cache；
- 生成 KG quality report；
- 对 200 条 REFERENCES 做双人标注；
- 重新运行正式 v2 实验。

历史 5.100.0 结果可以保留用于档案，但不能与 Provider 5.90.0 的 v2 结果直接混合
宣称同一实验设置。
