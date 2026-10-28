# Research notes — Experiment stage

## 锁定主张、当前状态与科学门槛

METHOD.md Working claim逐字未改，来源仍为`.argus/evidence/idea/group-verifier-dynamics/route.md` §2.3及`docs/acl-paper-plan.md`的锁定执行修订：有限复核预算下先跨真正task组覆盖，再在发现已接受错误的组内扩查，检验完整程序学习及单位复核成本收益。四项学习条款——学习改善、胜过同预算uniform、胜过强两级CARE、单位复核成本学习——均untested；机制仍仅原固定策略池的partial，不能用纠正数、训练分数、优势接近或本次描述性诊断代替。Egashira和CARE已有直接先例，原创性与触发相对覆盖的增益尚未建立。

candidate seed0真实完成63次optimizer更新后停止，不是100步完成；39题validation未运行，不评测step63。最终英文论文/PDF/LaTeX/可信完整比较/中文最终说明/下载包仍未交付，不进入Paper。100次真实更新与39题门槛不降低，常数组零优势更新不能凑进展。

## 当前决定性结果：candidate已有公开数据诊断

代码`research/analysis/diagnose_learning_signal.py`；唯一科学输入为`results/development/learning/candidate-seed0/train/attempt-001/public/`中的`rollouts.jsonl`、`raw_generations.jsonl`、`collections.jsonl`、`updates.jsonl`、`budget.json`及summary中的updates/collections/stop_reason。未调用模型、broker、私有候选执行、标签查询、参考重计或额外采样，未读CARE或兄弟诊断结果。没有改METHOD、公共训练源码、配置、原caps或旧结果；没有重建source snapshot/manifest/audit packet。

输出`results/development/learning/candidate-signal-seed0/attempt-001/`：`command.txt`、`inputs.json`、`runtime.json`、`log`、`exit_code=0`、`summary.json`和`report.md`。主计数闭合63更新、190 collections、380组/1520响应、434487生成tokens、183251训练tokens、607 strict购买；119行纠正中PROGRAM_FAIL111、RESOURCE_FAIL8。旧训练执行与末批tensor的真实性直接复用Reviewer已接受的`candidate-seed0/continuation-001/checks/independent-train-002/result.json`及`checks/stopped-batch-001/result.json`，未重跑验证器、整条执行数据审计、旧spec、smoke或基础设施调查。

全380组base全0/mixed/全1为105/165/110；部分纠正后为142/142/96。四种转移（始终无对比/原mixed保留/纠正消除/纠正新建）为204/131/34/11，消除均为mixed→全0；另有3个base全1→全0，也保留在无对比类。34个消除组中31仅PROGRAM_FAIL、2仅RESOURCE_FAIL、1两者都有。187个未购base-accepted行的union仍未知，部分纠正后全1不能称已知严格全通过。所有全失败、全通过和零事件组都在分母。

完整固定段主表由分析代码生成，见`candidate-signal-seed0/attempt-001/report.md`及`summary.json.segments`；段为1–20、21–40、41–60、61–63及停止批次，全部组保留在分母，不选择有利窗口。

142个保留组到达，126组用于63次更新、15组超出当次目标未携带、1组停止时未消费；不把超额组当成跨policy FIFO队列。`groups.jsonl`逐组保存原/部分纠正向量、已知union及未知槽、纠正类别、task出现次数、FIFO归属；`collections.jsonl`/`policies.jsonl`保存完整时间轨迹，`updates.jsonl`包含所有63个完成窗口及第64个右删失窗口，逐项给出collection、原始组、保留到达、FIFO使用、补采次数、等待及未消费组与费用。

policy_version=parent_update是生成前已完成的optimizer数，attempted_update=policy_version+1；更新输入policy_version0对应完成第1步，policy_version63对应为第64步补采，绝不归作第64步已完成。collection_index全程0基；replenishment_index为当前窗口0基。完成窗口等待collection数的直方图为1:12、2:20、3:11、4:10、5:5、6:4、8:1；最长连续零保留段4次。更新16实际用了8次collection，不能把偶发长等待一概说成无法恢复。

停止窗口collection182..189，保留序列严格为[0,0,0,0,0,0,0,1]，16组raw mixed4→partial mixed1。等待第8次才出现第一组，第二组到达时间右删失；前7次零保留。末批费用64响应、19290生成tokens、26 strict购买、10 PROGRAM_FAIL纠正、0更新，全部计入总账。strict余额2593、响应4880、生成tokens6119113，支持“直接停止边界是凑不齐保留批而非这些全程余额归零”，不支持“加大等待一定完成”。不按IID假设外推完成概率或所需窗口。

108个真实task每题3–5次曝光（3次57题、4次46题、5次5题）；272个相邻同task出现对，其中2对同policy，raw mixed变化-1/0/+1为39/200/33，部分纠正后为43/195/34。单题量小，重复响应不是独立task。`tasks.jsonl`、`task_policies.jsonl`、含零曝光的`task_segments.jsonl`及`repeat_changes.jsonl`保留所有条件统计。

初始流64 collections/128组、补采流126 collections/252组；按已接受108题、两题/batch合同切分的原记录分别为108+20和108+108+36个task曝光，各片内没有task重复。`sampler_exposure.json`仅检查已记录序列与epoch覆盖关系，不导入/重跑RepeatSampler，不重复已通过的126个采样对验证。

停止前364组raw/partial保留率0.442308/0.387363；将每题历史率按停止批实际task频数重加权为0.270833/0.229167，末批实际0.25/0.0625（16组、15题，Mbpp/626出现两次且按两次加权）。这说明末批task组成以往较少产生对比；原mixed的3次消除又进一步减少信号。前60步分段partial保留率约0.387/0.388/0.412，没有单调衰退。不能从时间/task相关及部分已购标签区分策略效应与采样波动，更不能推断学习改善、崩溃或触发机制因果。

辅助量仅用已记录长度和导出程序：全程生成长度均值285.846711、median264，固定段均值279.03/292.13/285.59/281.05/301.41；组内2280个exact-program响应对中重复0（包含空/无效提取，非语义等价判断）。原文件无finish_reason、EOS身份、明确截断或解析状态，故这些率以及entropy/梯度均不报告。

新增分析定向检查`candidate-signal-seed0/attempt-001/checks/test_diagnosis.py`，命令/log/exit_code在同目录：5 passed、exit0。只核新分析的部分标签、全部组分母、等待与FIFO/off-by-one、主表与已有账本、同task/组成及导出一致性；因本任务可写范围不包含tests/spec，检查位于获准输出目录，没有重跑旧spec。Reviewer已独立运行同一5项检查（5 passed、exit0），并仅用本臂六份公开输入独立复算全部主转移、64个等待窗口/FIFO、policy/task/固定段及频数加权数字；真实CLI空目录复跑13份科学导出内容一致。科学部分已通过，剩余仅本次访问归属补充的记录复核；本轮不再运行科学检查。独立观察见本任务round-0001审阅记录（created_at=1793084264.9329464）。

## 下一项单一选择（尚未执行，须Planner明确排程）

最有依据的缺口是固定8-collection窗在低对比task组成和已购纠正消除mixed叠加时，第二个真实保留组未及时到达；不是总预算耗尽。现有资料不足以在延长等待和修改学习配方之间选定已被验证的修复。

`candidate-signal-seed0/attempt-001/recommendation.json`给出一项最小共同判别trial：未来candidate/uniform/完整两级CARE各自从共同初始模型、新seed1、空付费状态独立启动；每窗先记录原8次边界，只在不足两组时给固定额外8次机会，最多16即止，全程硬caps保持原值。仅改变等待这一因子，不移旧标签/adapter/replay，不以常数组更新凑数；保持真正task、K4、2 raw组/collection、2 mixed组/update、原FIFO、信息边界、强uniform/两级CARE及共同调优机会。

逐臂报告所有未触发、触发后实际凑齐非零更新、16次仍失败和全程cap耗尽的窗口及费用，不能只报救回者。至少实际观察到前8不足、额外块凑齐并完成更新，才支持“有限等待可救回某些窗口”；无触发则此判别未测试，均未救回则不能支持该修复，不再自适应加窗。前8前缀是当前同policy下的直接观测，延长后的整条轨迹不是原8次完整训练的反事实。此试验不能独自证明学习增益或方法优越性。开发目标仍100次真实更新，只有达终点的臂可做原39题评测；任何未完成均保留，不评测中途checkpoint。

新预算须独立排程而非追加旧合同：每臂沿用strict3200/响应6400/生成tokens6553600/真实optimizer100/训练tokens2000000/feature10000000/selector_steps3200/examples2000000，上限最多800个完整collection，可能仍不足100步。三臂训练合计申请上限strict9600、响应19200、生成tokens19660800、optimizer300；三臂均达终点才另有117响应/117次strict、最多119808生成tokens的39题评测。单触发窗额外8次至多64响应、32购买、65536生成tokens，这些是设计上限而非观测结果/预计开销；不把总生成预算翻倍，也不以共享设备墙钟估算GPU小时。

相对共同改学习率，本试验不改变优化recipe且能直接观察第二组到达；没有梯度/entropy或单调衰退证据支持具体学习率调整。相对把每update目标降成1组，保留原训练暴露量及归一化合同，避免把更小更新当相同进展。额外等待是否足够、三臂差异和最终39题效果均未验证。运行中的96a7237f3242 CARE继续原合同及原绑定，不读结果、不改配置、不取消；任何未来共同变更只由Planner排程，不能回溯改写该任务。

## 指定访问争议：任务归属已映射，CARE会话/调用仍缺失

原提取记录按其观察时点保留：指定candidate会话01a17bb4-3e01-7a53-a23b-4edb6ffdcbcc的204条原工具输入（04:52:57.340–06:33:51.672 UTC）及争议前后60秒的5个调用已导出；care-seed0字面路径匹配0。旧access_attribution.json、attribution.md、named_session_tool_inputs.jsonl及extract_attribution.py原字节未改，没有重跑旧提取。

Reviewer于2026-10-27 06:57:44.932946 UTC记录的独立审阅中报告：其环境已可读取给定`state/projects/s-efe9deac/events.jsonl`，并直接核对争议事件。该审阅的实际事件文件读取时刻未单独给出，不将审阅记录时间或事件时间冒充读取时刻。Engineer本次环境同一路径仍不存在，故以下明确引用Reviewer直接观察；不回溯改写先前“文件缺失”的观察，也不将不同环境的可用性当作矛盾科学测量。

争议事件ts=1793077565.096486（2026-10-27 05:06:05.096486 UTC），Reviewer观察时第3041行：type=engineer.progress、kind=command_execution、item_id=96a7237f3242、actor=engineer-r1。原命令为`/usr/bin/bash -lc 'tail -n 2 results/development/learning/care-seed0/train/attempt-001/public/saves.jsonl; ls results/development/learning/care-seed0/train/attempt-001/public/checkpoints/checkpoint-10'`，仅按Reviewer所见引用，未执行或打开所指文件。item_id直接将此命令归CARE任务96a7237f3242；相邻candidate事件标在02b9996432a5，第3390/3392行的role.session.turn / round.main.completed元数据将candidate任务02b9996432a5对应至指定session 01a17bb4-3e01-7a53-a23b-4edb6ffdcbcc。任务层归属已有直接证据，不能仅凭共享actor名称或混合摘要把该CARE任务命令归给candidate。

剩余缺口明确限于CARE精确session_id、原始call_id及事件到具体CARE会话/调用的映射；Reviewer可见启动/round元数据未给出这些值，本轮不读取其他会话或CARE-owned目录补全。任务映射不等于精确CARE会话映射，也不证明所有工程访问都不曾发生；模型Landlock隔离及204条输入的字面匹配0均不是全面隔离证明。此CARE任务事件未建立candidate协议偏差；不作更广的访问缺失保证。Reviewer也已独立重提取指定candidate的204条输入及5个邻近调用并与旧导出逐条一致，本次直接复用。

新增append-only证据为`results/development/learning/candidate-seed0/continuation-002/attribution_supplement_001.{json,md}`，含Reviewer观察来源/时间范围、原事件/命令、任务映射及剩余缺口；新增脚本`append_attribution_supplement_001.py`及supplement_001命令/log/exit_code=0保留。`supplement_001_preservation.json`确认原continuation-002八份记录及父目录/continuation-001六份report/claim_status/CHECKPOINT共14份旧文件字节不变。原补充前记录及科学输出保留，根claim仅同步这项记录边界，四项学习untested、机制partial不变。Reviewer现在只需复核本项补充；不重跑科学分析、旧spec或训练验证，不读CARE结果，不另建调度任务，不变更当前CARE合同或设备绑定。

较早256/1024长度访问归属是另一具体争议，不能套用来解决本项。其已有映射只见`results/development/allocation256/attempt-002/command_attribution.json`及同目录check_command_attribution.py；本次为定位会话记录路径仅读该提取脚本，没有重开旧长度结果。

## 仍可复用的已接受资产及边界

原固定256-token池：`results/development/length256/attempt-002/public/`，108题/432响应、28 semantic与1 resource纠正、191次付费union，固定初始策略，不是学习结果。`results/development/allocation256/attempt-002/summary.json`：32个allocation partitions，candidate/uniform平均semantic纠正24.34375/22.65625、同等模拟购买154.78125；平均每task优势残差差-0.00864401943，collection有利/相等/不利74/1619/35。1196/1728局部pool饱和；固定池重复分区不是独立训练seed，不隔离触发与覆盖，也不比较CARE。此为机制partial的原来源，不用本次119个纠正升级因果结论。

共同入口与配置的既有工程验证：`results/development/learning-setup/spec/focused-004/`21项通过；真实train3更新、同配置resume额外1更新及两题export在learning-setup对应目录，全部费用包括首次export和重复导出。原resume-validator将累计72响应与新增16行误比的缺口及首次eval canonical缓存写出偏差保留在该任务旧证据中；不是本次诊断修复或重新验证的事项。此前readiness/正控制/全canonical准备/隔离的已接受结果继续复用，不重跑，不把短CARE Stage2=0当强baseline已充分训练。

环境与可复用基础设施决策已记录`.agents/skills/rl-training-infrastructure-decision/SKILL.md`（Surveyed2026-10-26，2026-11-09或任务/硬件变化重核）和`.autors/s-efe9deac/wiki/pages/infrastructure/shared-training-readiness.md`（仅256-token readiness范围）。源码/配置/模型与数据revision和官方来源仍由既有固定记录、requirements.lock、third_party及research/GROUND_TRUTH.md持有；本任务CPU静态分析不选择新基础设施、模型或API，不开展新调查，不写Skill/Wiki，不创建旧审计替代物。
