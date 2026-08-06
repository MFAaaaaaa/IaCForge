# IaCForge 方法细节

IaCForge 的核心流程是：

```text
Prompt + 选定 KG
  -> Planner
  -> 资源/依赖 Graph IR
  -> 精确 AWS Provider Schema grounding
  -> Compiler
  -> HCL
  -> terraform validate
  -> terraform plan
  -> OPA
```

KG 固定进入 Planner 和 Compiler 两个阶段，不提供注入阶段选择。

## 生成阶段输入

Planner 有两个逻辑输入：

1. Prompt
2. Full KG 或 Paper KG 的检索证据

Compiler 有四个逻辑输入：

1. Prompt
2. Graph IR
3. Schema context
4. KG 表示

Full KG 在 Compiler 阶段使用由 KG 证据和 Schema 共同形成的 typed Provider Contract；Paper KG 在 Compiler 阶段使用原始检索证据。

## Full KG

Full KG 覆盖打包 AWS Provider Schema 中的全部 resource/data source 节点。关系边来自官方文档示例、Schema 关系和保守的属性引用规则。运行时只根据可见 Prompt 做确定性的资源别名/Schema 文档短语匹配、概念集合补全、排序和有界依赖闭包。

## Paper KG

Paper KG 复现论文中的文档、argument/block、example 与 REFERENCES 遍历，并使用打包 Chroma 索引。该 KG 含 benchmark-scoped 的泄露关系边，因此只作为泄露分析条件。两阶段运行时检索仍只使用可见 Prompt，但这不会消除图中已有的泄露。

## Graph IR 与 Schema

Planner 输出资源实例、依赖边和实现说明。解析器完成 JSON 提取、资源类型规范化、实例名规范化、依赖端点检查与去重。Schema grounding 以 Graph IR 中的资源类型为精确键，只返回这些类型的 required、optional、computed-only 和 nested-block 信息。

## HCL 与本地修复

Compiler 生成完整 HCL。规范化只补充缺失的 Terraform/AWS Provider 配置和离线 plan 设置，不生成任务资源或属性。

可选 local repair 只在初始候选无法 validate 或 plan 时调用一次。输入为 Prompt、Graph IR、Schema context、当前 HCL 和 Terraform diagnostic；不输入 KG、Provider Contract 或 OPA 信息。修复后重新执行 validate 与 plan，成功后才进入 OPA。

## 结果

`results/` 保留七个模型的 Baseline、七个模型的 Baseline + IR + Schema、Qwen2.5-Coder 3B/14B 的 Baseline + IR，以及 Full KG、Paper KG 各自启用/不启用 local repair 的八组 KG 结果，共 24 组完整实验。CSV 与日志内容保持原实验字节不变，`RESULT_MANIFEST.json` 记录路径、哈希和指标。
