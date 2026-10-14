Chemistry Lab 采用统一的设备发现、状态读取和能力调用形式。Anthropic 于 2026-08-27 公开的 MHS 研究预览介绍了通用驱动、读写操作、设备描述及通过 MCP、命令行或 API 访问设备的设计。本项目在此基础上补充具身仿真所需的几何绑定、物理语义和实现证据。截至本规范编写时，没有用正式 MHS SDK 对此格式进行兼容性验证。[官方介绍](https://www.anthropic.com/news/model-hardware-standard-research-preview) 与 [预览入口](https://modelhardwarestandard.com/) 是关系说明的依据。

以下 lab.* 是本项目要求适配器遵循的逻辑接口，不是 Anthropic SDK 语法，也不表示安装此 skill 后已经存在名为 lab 的可运行对象。可以映射到 Python、JSON RPC、MCP 或其他传输方式，语义和字段保持一致。合同中的 binding.invocation 记录实际已有入口，表格同时显示两者。

| 逻辑入口 | 语义 |
| --- | --- |
| lab.describe(asset_type_id=...) | 返回允许发现的类型、组件、可用能力、参数、状态槽和限制，不包含实例值。 |
| lab.describe(instance_id=...) | 返回该实例对应类型、版本、适配器和当前可用能力；两种标识参数互斥。 |
| lab.read(instance_id=..., state_ids=[...]) | 返回授权范围内同一状态版本的观测，不推进仿真时间，不产生隐式写入。 |
| lab.invoke(instance_id=..., capability_id=..., arguments={...}, request_id=...) | 校验目标、能力、权限、参数、前提后调用实际适配器。 |
| lab.write(instance_id=..., state_id=..., value=..., request_id=...) | 仅对 read_write 槽，通过 write_via 路由到声明的能力，不能直接修改内部真值。 |
| lab.poll(operation_id=...) | 查询异步操作；operation_id 与实例和调用者绑定。查询本身不重复执行动作。 |
| lab.cancel(operation_id=..., request_id=...) | 仅在能力声明支持时请求取消；返回取消状态及已发生的部分效果。 |

instance_id 由世界或设备注册表分配，不能用进程全局单例承载所有同型号设备。每个实例独立拥有状态、容器内容、操作日志和资源锁；仿真世界使用共同宿主时钟。需要 sim.step 或 sim.reset 时，将其登记为宿主 simulation 能力，限定到明确的世界和实例集合。真实硬件适配器不提供重置物理世界或直接设置物理真值的捷径。

请求与结果使用明确结构，下面是协议示意。调用加热目标的同步成功只说明控制目标写入完成，温度稳定需另一个观测条件。request_id 在同一个逻辑请求的重试中不变，新请求必须换 ID。

```json
{
  "protocol_version": "1.0.0",
  "instance_id": "hotplate_01",
  "capability_id": "thermal.set_temperature",
  "arguments": {"temperature_K": 333.15},
  "request_id": "request_20260908_001"
}
```

```json
{
  "protocol_version": "1.0.0",
  "instance_id": "hotplate_01",
  "request_id": "request_20260908_001",
  "operation_id": null,
  "status": "succeeded",
  "state_revision": 12,
  "clock": {"domain": "simulation", "time_s": 20.0},
  "result": {"setpoint_K": 333.15},
  "observations": {},
  "error": null
}
```

对于 read，observations 以 state_id 为键，每条保存 value、unit、quality、sample_time_s 和 sample_window_s。quality 为 valid、unavailable、stale 或 estimated，缺测用 value=null 加具体原因，不能返回零伪装传感器读数。sample_window_s 为 null 或明确的区间起止值；区间平均 FID 电流需保留窗口，不能标成瞬时电流。series 数据另声明轴、样本形状、时间域、单位和采样间隔；大数据可以返回带元数据与访问控制的流句柄或文件引用。

同步结果为 succeeded、rejected、unsupported 或 failed。异步操作先返回 accepted 与 operation_id，后续为 running、succeeded、failed 或 cancelled。接收到 accepted 不等于样品已转移。超时或失联后若结果不确定，保留 operation_id 并查询，不以新请求自动重放不可重复动作。same_request_id 要由适配器维护请求与参数摘要的去重记录，同一 ID 配不同参数必须报冲突；重启后去重记录丢失时不能声称提供跨重启的恰好一次保证。

固定错误包括 UNKNOWN_INSTANCE、UNKNOWN_CAPABILITY、UNSUPPORTED、INVALID_ARGUMENT、PRECONDITION_FAILED、ACCESS_DENIED、BUSY、TIMEOUT、REQUEST_CONFLICT、DEVICE_ERROR。领域错误可补充明确命名的代码。错误结构保存 code、message、retryable、side_effects 和已知进度；不能向 agent 的错误文本塞入受限组分或真实质量。取消和失败均按 execution.partial_failure_zh 解释已发生的效果，不能隐式回滚真实物质转移。

代理权限来自宿主身份与访问策略，不接受调用者自填 evaluator 身份来越权。describe 隐藏不可调用能力；read 和 invoke 的返回、事件、日志、数据文件均执行同一可见性过滤。调试文件、类型示例中的合成数据和真实任务真值分开部署。只限制工具参数却让代理可读取全部样品真值文件，不能宣称完成了隔离。

供未来 SDK 实现的 Python 类型边界可以保持简单：

```python
from typing import Any, Protocol

class LabAdapter(Protocol):
    def describe(self) -> dict[str, Any]: ...
    def read(self, state_ids: list[str]) -> dict[str, Any]: ...
    def invoke(self, capability_id: str, arguments: dict[str, Any],
               request_id: str) -> dict[str, Any]: ...
```

实例寻址、身份、时钟和资源锁由宿主包装；每个 adapter 只操作自身实例。write 统一路由到 invoke，poll/cancel 只在支持异步的适配器中提供。此类型定义没有驱动实现，不能单凭结构符合就宣称接口已可用。以实际调用、可观测完成、失败行为和模型求值结果作为验收依据。
