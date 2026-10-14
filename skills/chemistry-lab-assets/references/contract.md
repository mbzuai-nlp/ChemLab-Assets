Chemistry Lab Asset Contract 1.0.0 使用 UTF-8 JSON。一种仪器配置一个合同，建议放在 assets/<asset_type_id>/asset.contract.json。Blender 场景、碰撞代理、物理求解代码和证据文件通过 artifacts 关联。文件路径相对资产根目录，不能通过 .. 或符号链接逃出指定根目录。历史版本保存在当前索引扫描目录之外；活动集合中同一 asset_type_id 只保留一个版本。

| 合同字段 | 含义与规则 |
| --- | --- |
| schema_version | 合同结构版本，目前为 1.0.0。 |
| asset_type_id | 稳定的型号及配置标识，例如 agilent_7890b_ssl_fid；与实例、文件名和场景对象名分开。 |
| asset_version | 该资产内容版本，采用 major.minor.patch。同样名称下不能无提示改单位、参数含义或绑定配置。 |
| name_zh、category、asset_kind | 名称、类别与 instrument / passive / tool / robot / accessory。category 可扩展，不预设 200 种固定分类。 |
| coverage | full 表示本配置功能组件已完整登记；subset 是明确节选；scaffold 是待实现的起始描述。 |
| coordinate_system | 长度单位 m、角度 rad。status=defined 表示已有明确坐标，assumed 表示尚待验证的约定；没有几何、模型绑定或空间端口时用 not_applicable，手性、上轴和 frame 均为 null。 |
| artifacts | artifact_id 到路径、格式、available/planned、可选 SHA256 的映射。格式可以是 blend、usd、urdf、glb、py、json 或其他实际格式。 |
| sources | source_id 到资料类型、可访问引用、定位信息和证据说明的映射。定位信息写页码、图号、代码入口或测量记录。 |
| components | component_id 到功能、物理意义、父级、几何目标、物理属性和能力引用的映射。父级只表示装配树，流体、电路或数据关系通过 ports 及 mechanisms 记录。 |
| state_slots | 稳定状态路径到类型、单位、来源、可见性、读写方式、采样含义和绑定的映射。 |
| capabilities | capability_id 到输入、输出、作用对象、前提、状态和模型变化、执行语义及验证的映射。 |
| model_bindings | 将组件和状态对应到实际关节、位姿、材质、显隐或屏幕目标；记录同步权威和方向。 |
| ports | 带所属组件、介质、方向、坐标系和连接约束的端口定义。实例之间实际连接由世界状态保存。 |
| mechanisms | 需要仿真的物理或化学机理、涉及状态、方程、参数、实现程度及适用边界。纯结构器具可以为空。 |
| limitations | 整个资产的已知近似与未完成范围。 |
| extensions | 扩展字段使用带点的命名空间，例如 chemistry_lab.reaction_model；不要在标准字段内静默塞入私有含义。 |

所有 ID 使用小写字母开头，可包含数字、下划线以及由点分隔的路径。组件 ID 在资产类型内稳定，跨实例通过 instance_id 区分。模型改名时只改 selector 或 target。对于可替换耗材，用接口兼容性和组件关系描述替换结果；不得通过直接修改同一组件 ID 的物理含义来假装换成另一种部件。

物理属性保存 value、unit、meaning_zh、evidence、source_ids 和 uncertainty。unknown 对应值通常为 null。照片只支持可观察的形状或位置估计，不能证明流量、热容、摩擦、惯量、精度或响应系数。估计量与假设参数明确标记，并给出用于哪一层模型。schema 允许扩展物理属性，不能据此将未经实现的方程描述写成数值求解能力。

| 状态槽字段 | 约束 |
| --- | --- |
| value_schema | 值的结构与类型。数字不接受布尔值；数组需描述元素类型，复杂数据需明确字段和轴。 |
| unit | 数值使用 SI；计数、比例和布尔标志用 1。不同单位需要显式转换，不改变公共槽的含义。 |
| reference_conditions | 气体体积流量写实际、标准或参考状态及温压条件；压力写绝压或表压；关节和向量写参考系。无此类依赖可为 null。 |
| origin | setpoint、process_truth、sensor、derived、scene。没有实现噪声模型的理想温度，不能直接宣称为实测传感器读数。 |
| visibility | agent、debug、evaluator；process_truth 使用后两者。代理获准使用的理想化读数应单独建立可观测模型并说明其来源。 |
| access | read_only、read_write、internal。读写与可见性是两个维度。 |
| read_binding | 真实数据来源，指定后端、artifact_id 和 accessor；未实现则 null。accessor 是可审查描述，校验器不会执行它。 |
| write_via | read_write 槽必须绑定 capability_id、参数名和固定参数；该能力的前提、单位、错误和权限同样适用。 |
| sampling | instant、interval_mean、integral、series、static 及 simulation / wall / none 时间域。平均量、积分量或序列需写窗口含义。 |

输入和输出使用 JSON Schema 的可移植子集。能力输入顶层为 object，properties 定义参数，required 定义必填，additionalProperties=false 拒绝未知参数。参数可用 type、enum、const、minimum、maximum、exclusiveMinimum、exclusiveMaximum、items、minItems、maxItems、properties、required、additionalProperties、anyOf 及本地引用；具体可校验关键词以工具的错误提示为准。数值输入叶子使用 x-unit，流量等额外注明 x-reference-conditions；description 解释参数含义，default 只在适配器实际采用该默认值时填写。未知关键词不可由校验器静默当作已校验。输出的数值单位在 x-unit 或对应状态槽中明确，禁止把无单位的数组交给调用者猜测。

| 能力字段 | 约束 |
| --- | --- |
| kind | query、device_command、robot_skill、simulation、analysis、configuration。初始化配置与运行中可调参数分开。 |
| visibility | 决定能力及返回值面向 agent、debug 或 evaluator。agent 能力不能直接输出受限槽。 |
| availability | declared 只有设计，implemented 有实际可调用入口，unsupported 明确不支持。 |
| implementation | process、scene、robot、hardware、host 分别为 absent、declared、implemented、verified、not_applicable。不得用单一完成标记覆盖所有层。 |
| binding | 后端、实现文件、真实入口和可审查调用示例。只是引用，不由合同自动执行；declared/unsupported 通常为 null。 |
| preconditions | 描述、涉及槽、实际检查表达和 enforced_by。未实现检查用 not_enforced，不能当成厂家互锁。 |
| state_effects | 每个变化的 state_id、意义和 immediate / on_step / on_completion / external 时机。无变化使用空数组。 |
| observes | 该能力实际返回或用于完成反馈的槽引用；与 output_schema 一致。 |
| model_effects | mode 为 none、direct、coupled、host_required，必须描述可见变化或无变化原因。none 的 binding_ids 为空。 |
| execution | 同步或异步、时间域、完成判据、超时、取消、重试、资源互斥和部分失败含义。 |
| errors | 稳定错误码、物理或操作含义，以及 none / partial / unknown 副作用。拒绝动作是否保持状态以实现为准。 |
| example_arguments | 至少一组符合输入结构和范围的示例；示例不等于已执行测试。 |
| verification | 检查 ID、层、结果、方法、环境、时间、证据文件与说明。passed 支撑相应层 verified，不能跨层借用。 |

复用同义能力，例如 power.set、thermal.set_temperature、acquisition.start、acquisition.stop、sample.aspirate。领域特有能力可以使用 chromatography.*、electrochemistry.* 等命名空间。只有语义和单位相同才复用；质量比例进样应保留 fraction，不得包装成未经校准的 volume_uL。可动器具、耗材和机器人共享物质转移时，宿主记录供体、受体、量、单位及事务 ID，并按声明处理部分转移；不能因一次调用失败就假定样品已回到原处。

model_bindings 的 sync_status=unbound 对应 direction=none、authority=none，表示几何目标可存在但没有跨层同步。已实现的 process_to_scene、scene_to_process、host_to_both 分别由 process、scene、host 负责权威状态。被动场景开门能力可直接操作关节，仍保持过程同步 unbound。动画和碰撞求解是否完成单独验证；有 URDF 或碰撞代理文件不意味着已完成接触测试。

版本更新中，修正文档或非行为问题增加 patch；向后兼容的可选能力增加 minor；删除、改名、改单位、改变参数或错误的含义增加 major。schema_version 管结构，asset_version 管具体资产，实例日志记录两者以及使用的代码和几何版本。合同之外的实例状态不能写回类型模板。

静态工具检查类型、字段、引用、示例与部分逻辑一致性，可选检查文件与哈希。它不会运行 accessor、导入任意驱动、检查 Blender 目标是否实际存在或确认物理正确性。真正发布 implemented/verified 能力时，另行验证真实调用与实际返回；字段拼写正确但运行对象不存在也必须算失败。
