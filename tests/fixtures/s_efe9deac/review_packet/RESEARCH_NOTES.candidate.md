# Research notes — Experiment stage

## 科学状态与下一科学任务

METHOD.md Working claim、falsifier、模型/数据、学习率、K4、两raw组/collection、两mixed保留组/update和原8-collection等待窗未变。路线仍为`.argus/evidence/idea/group-verifier-dynamics/route.md` §2.3及`docs/acl-paper-plan.md`的锁定执行修订。学习改善、胜过同预算uniform、胜过强两级CARE、单位复核成本学习四项仍untested。机制仅原固定策略离线池partial；原来源`results/development/allocation256/attempt-002/summary.json`中candidate-minus-uniform每task优势残差均差-0.00864401943，collection有利/相等/不利74/1619/35。32个重复分区不是独立训练seed，不隔离触发与覆盖，也不包含CARE。Egashira和CARE已有直接先例，原创性与触发相对覆盖的增益尚未建立。

仍处Experiment，不进入Paper。100次真实更新与预定39题终点评测门槛不降低，常数组零优势更新不能凑进展；完整英文论文/PDF/LaTeX、可信公平比较、中文最终说明和下载包尚未交付。

复用上游已审开发结论，本轮未重读兄弟臂私有输出：CARE完成100次真实更新及39题validation，17/39通过，Wilson95为0.29305–0.59024。两级探针得到自然监督并实际更新；训练回放拟合不等于独立泛化或强基线已充分确立。candidate完成63次真实更新后insufficient_retained_batch停止，没有100步终点，没有39题终点评测，也不评测step63。旧HF开发结果不能与迁移后引擎臂拼成公平胜负。

candidate公开诊断入口仍为`research/analysis/diagnose_learning_signal.py`与`results/development/learning/candidate-signal-seed0/attempt-001/`；沿用已审静态复算，不重跑：380组、1520响应、434487生成tokens、183251训练tokens、607次strict购买闭合。原mixed纠正消除34组、保留131组、新建mixed11组；部分已购标签不能当全部union真值。末窗collection182..189保留序列[0,0,0,0,0,0,0,1]，费用64响应/19290生成tokens/26strict、0更新；直接边界为第二个保留组未及时到达，全程余额未耗尽，不能推断延长等待必然完成。

只有基础设施验收后，才进入Planner单独排程的共同8+8等待判别：candidate/uniform/完整两级CARE在同一新栈、共同初始模型、新seed1、空付费状态下，各自前8次不足时给固定额外8次机会，最多16；记录全部未触发、救回、仍失败及cap耗尽窗口。保持原科学合同和全程caps；达100步才做原39题终点评测，不迁移旧标签/adapter/replay，不把该判别与引擎修复合并归因，本轮未执行它。

## 当前共同接入：真实系统已运行，概率验收仍失败

共同`.venv`仍为Python3.12.3、torch2.11.0+cu129、Transformers5.5.3、PEFT0.19.0、vLLM0.24.0、TRL1.17.0.dev0。TRL保持4c9e04a0328deb0b773748d6bce3f27381458c71，vLLM官方v0.24.0固定到ee0da84ab9e04ac7610e28580af62c365e898389；第三方TRL/CARE/EvalPlus/vLLM源码未改。requirements.lock固定实际环境，根pyproject仍指历史环境且不在可写范围，重现新环境用锁文件。原锁、安装前版本、失败安装、官方实时来源及最终版本在`results/infrastructure/shared-vllm/migration-001/`的requirements.before.lock、installed.before.json、sources/access.jsonl、versions.final.json等原记录。复用2026-10-26调查，没有重做九框架survey，没有新增依赖或更换模型。

迁移继续使用固定TRL的GRPOTrainer、VLLMGeneration.sync_weights/generate、继承的原生DAPO和官方重要性修正；没有自写serving/training loop。recipe.use_vllm为真实开关，保留HF与公共七臂CLI。变长token/sequence logprobs、mask、IS元数据、标量分母的FIFO截取合并、真实task uid、逐行付费、CARE零优势槽均保留。METHOD.md只补充引擎Protocol/诊断配置入口，未改科学claim或falsifier。

原最终栈official/attempt-004与hf/attempt-001各1步、candidate parity/002/004/005各2步仍为真实更新资产，official003是旧PEFT的额外历史1步。工程迁移本轮新增optimizer为0。六个其他arm的真实更新、实际小型resume和AB/BA/AB三对candidate两步计时仍未运行。

## 已接受的修复与此前明确范围

`trainer.py:preserve_frozen_parameters`保留官方合并/传输/拆开路径，finally清理尚合并状态，原位恢复同步前冻结参数；不恢复可训练LoRA，不替换Parameter/optimizer。真实CPU组件复现从基础模型q_proj和迁移parity/attempt-004/checkpoint-1读取实际权重：2359296元素中未保护改变444个，最大7.62939453125e-6；零B控制0、保护后0。来源`migration-002/cpu-merge-001/{check.py,tensors.pt,result.json}`；与Reviewer直接反馈一致。官方PEFT0.19.0源码URL/实时访问/本地匹配见`migration-002/sources/access.json`。

真实diagnostic001/002/003中保护后的HF评分在同步前后及重复评分时均逐值一致，base恢复、LoRA不变。未保护同步的HF漂移按默认+FP32、batch-invariant+FP32、batch-invariant+BF16设置分别为0.08222770690917969/0.11154747009277344/0.15535926818847656，属于不同明确标记设置。默认001改变275626个冻结元素，002/003改变275520个，max均7.62939453125e-6。Reviewer已独立接受该actor副作用修复，不能继续把它作为未修复原因，也不能把它解释为全部概率残差。

此前测试夹具误入生产范围的假阳性已修复并通过独立复核。保留但不采用`migration-002/raw-final-001`下verify-final-001/wrapper/timing的旧概率通过字段和303562 feature/552 response混合上界。现在allocator/verifier共用owned_allocation_paths，按显式root分区，坏/缺配置保守占用；概率只能来自本任务已完成、raw核验和退出检查通过的allocated producer。孤立张量和其他根的夹具不采纳，不按目录名字猜。当前pytest夹具位于tests/spec/.runs，不混入生产结果。旧测试夹具与错误输出未删除。

## 最新决定性控制：官方实际合并权重仍不能通过

按Reviewer建议，新增`trainer.py:observe_native_parameter_stream`，在未修改的官方`_iter_named_params`仍处于merge上下文、实际参数尚未yield给原生loader时评分HF；原生迭代与传输值不替换。保存并逐值核对实际yield的第一层q_proj与评分时HF该层权重，2359296个BF16元素完全一致。观察器评分异常也关闭原生流，并由既有冻结参数保护清理状态。该代表层检查不代替对引擎加载后全部权重的独立核查。

显式配置为`configs/infrastructure/shared-vllm/merged-reference-diagnostic.yaml`，capture_merged_reference=true；保持官方VLLM_BATCH_INVARIANT=1、FP32输出投影、相同attention/teacher-forcing约定、相同保存tokens和本迁移checkpoint-1的非零LoRA。不调用train/optimizer。源码、实际命令和原始输出在`diagnostic/attempt-005/`及`migration-003/launch-merged-002/`。worker退出0，外层公共parity命令退出1；计入全部两次新增HF评分，实际24响应/24生成tokens/25296 feature tokens，其他工作维度0。

成功重试的每个比较均1460个相同有效token。initial：未合并HF对vLLM和实际合并HF对vLLM的最大误差都为0.09167993068695068，合并HF对未合并HF为0。current：未合并HF对vLLM为0.11764097213745117（3个token超过0.1），实际合并HF对vLLM仍为0.11151862144470215（2个超限），合并HF对未合并HF为0.08226203918457031（0个超限）。三种比较的[0.8,1.2]外比例均0。

这不支持“BF16折叠是全部解释”：在官方实际合并状态下，HF/vLLM差异仍超0.1。合并确实改变当前HF概率，但不能据最大值相减计算贡献比例或指定剩余全部来自某个内核。未合并HF和vLLM的initial/current原始张量与先前diagnostic002逐值完全一致，观察器未改变这些主比较。没有换用合并参考来通过验收，primary仍为未合并训练策略；冻结阈值、mask和token集合不变。

raw控制位于`diagnostic/attempt-005/producer/public/merged_{initial,current}_hf.pt`、`transferred_q_proj_{initial,current}.pt`及原parity/*.pt。raw复算脚本、完整ratio均值/分位数/极值、计数和原生传输检查在`migration-003/raw-recompute-001/{check.py,result.json,log,exit_code}`，exit0；不调用模型，不能称为正式独立Reviewer。新raw verifier的diagnostic.merged_reference_comparisons另给相同矩阵和sequence诊断，primary验收没有改用该辅助字段。

## 本批中断与退出证据缺口

最初`diagnostic/attempt-004`也使用同一配置，原detached命令在`migration-003/launch-merged-001/launch.sh`。后续continuation时发现记录的worker及子进程都不在、GPU3无该计算上下文，但原父/子及外层退出码均未留下；仅初始两次HF评分保存，账本有3888 feature且无未决reservation。退出原因未知，不写exit0/-9或推断OOM/瓶颈/宿主限制。`termination_observation.json`保留实际进程/UUID观察；原文件未覆盖，没有对该目录重启生产。

原004完整预分配仍保守占用：24响应、24生成tokens、50000 feature、0 optimizer；没有把已知3888当作全部消耗。新空attempt005在重新核算额度、确认既有生产者不活跃后只重试一次，并实际完成上述矩阵。退出前的实时环境读取在005已结束后返回None，此事实保留；实际mask/UUID在其public/device.json、launch/command及设备采样中，batch-invariant执行路径在实际日志与配置/代码中留证，不伪造成功的进程环境采样。新005有真实parent/producer退出记录；旧004依然没有原生退出码。

因此不能报告全部退出记录正式通过。当前核验的producers_exited为untested，明确错误仅原004未记录退出；当前已记录PID的live列表为空。请Reviewer核对这份有限终止观察及保守费用，决定原004还需什么源证据；不降低检查器条件、伪造退出码或把未知完成当成功。

## 测试、总额度与后续阻断

先写测试，`migration-003/spec-transfer-red-001`真实2失败，`spec-merged-red-001`真实3失败；修复后`spec-merged-001`为58 passed。最终`migration-003/spec-final-001/{launch.sh,log,exit_code}`为128 passed、9 warnings、exit0（42.50秒），无skip/xfail；含实际PEFT merge上下文观察/异常关闭、真实传输张量/收费反例、辅助参考不能通过失败primary、原配置/FIFO/CARE分母/schedule/reference/resume及所有既有spec。依赖未变，pip check沿用同一栈已通过的migration-001/pip-check-final-002和此前Reviewer独立复核；不拿旧21项当验收。

最终公共raw核验`migration-003/verify-final-001/output/result.json`、wrapper `verify-wrapper-001/output/verification/result.json`均exit1；official/HF/initial过，updated/stale失败，producers_exited为untested（原004缺原生退出），七臂/resume/paired_timing未测。`timing-gate-001`exit1，无新生产allocation或模型调用。真实任务共有15个allocation，包含失败和两次本批零更新尝试，不含测试夹具。主概率准则仍为ratio区间[0.8,1.2]外<=0.01且最大误差<=0.1，必须同时满足；同prompts/IDs/mask，temp1/top-p1/无processors，排padding，含EOS/截断。native采样0.8/0.95及其IS单独记录。

累计保守上界strict165、response576、generated209946、optimizer13、training56135、features278858、selector_steps64、selector_examples20208，各维度未超授权总caps；13为计费上界，不是成功13步，本批新增optimizer0。来源为本批verify-final-001/output/result.json.aggregate_upper_bound，含原004全部50000 feature和24/24保守占用。`migration-003/remaining-allocation-final.json`给出剩余六其他臂各1步、六计时各2步、resume1步的逐次上限；13+19=32，计划合计strict677/response1600/generated1258522/optimizer32/training190135/features1578858/selector896/examples280208。算式不是这些运行完成或保证能够完成；以后每次重新核算所有维度，不追加未计划optimizer。

下一实际数值目标为区分引擎加载后的真实参数/布局与评分、projection、attention等数值路径；现有合并HF控制已排除“只要换成实际合并HF就全部通过”。不要重修已接受的actor恢复，不把辅助参考晋升为primary，不放宽阈值或筛超限token。任何新模型forward/生成须预分配，可优先利用保存tokens和本臂checkpoint，不扩规模。数值及退出证据未满足前，六臂smoke、实际resume、三对同卡计时、未来共同配置及引擎决策继续阻断。没有提速、学习或优越性结论。

## 文档、学习与访问范围

未创建learning_vllm_common.yaml，未改旧HF配置/结果，未进入Paper。基础设施Skill仅补已实际验证的冻结恢复与官方merge上下文内测量程序，不将数值假设或未通过引擎晋升为共同选择；旧Wiki仍是其2026-10-26/256-token历史范围。原始source/pins与已审依赖调查保留，不扩建审计或监控项目。

只用预定前8个真实training任务和本迁移token/LoRA；不读validation/test模型结果或标签，不评测39题，不导入兄弟标签/adapter/replay。旧candidate访问争议按已审`candidate-seed0/continuation-002/attribution_supplement_001.{json,md}`保留精确CARE session/call缺口，不升级为全面隔离保证；本轮未重开旧会话或兄弟私有输出。上轮工程当时看不到events.jsonl，Reviewer随后可读；不据缺失撤销其观察。

已披露例外继续保留：早期official callback漏reference_cache_dir，曾使用data/private/reference_cache超写边界；未删除/回溯改写，现callback/runner用attempt私有缓存并有spec。diagnostic001初版未装Landlock、未预设task内cache，不称始终隔离或未发生外部缓存写出；具体写出未追查，不清理掩盖。随后002/003以及本批004/005在初始化前设task内runtime cache、模型/CUDA后实际安装原Landlock，checkpoint仅精确开放自身叶路径，原isolation与device记录保留。正式独立审阅仍由宿主执行，本轮自检不称独立Reviewer。
