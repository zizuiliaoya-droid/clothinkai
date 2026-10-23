# 2026-10-23 phase2 dependency integration delta — unreviewed

Root authorized this local correction on 2026-10-23 after actual PR8 source binding rejected eleven legitimate credential-module Python files. The original phase1 R1–R3 issues remain closed; these new bytes do NOT inherit that approval and must be independently reviewed as part of phase2 round 1. Production remains disabled.

The original nineteen files and pins are preserved byte-for-byte with non-executable `.historical` suffixes at `../release-flow78-phase2-1021/baselines/phase1-r3-history/`. Original pins SHA256: `75875972f393f70ae828db7091e474de61e0687ebff7e6d5f8d4a40a5205fece`.

Only `release_builder.safe_name` receives an exact eleven-path public-code exception to its sensitive-component regex. All canonical-name, source Git commit/tree/mode/size and public-bytes secret checks remain. No recursive directory exception, private-content exemption, alternate executor or monkeypatch is added. The new targeted `test_phase2_credential_paths.py` checks the eleven public paths, committed source inclusion, secret contents, Git symlink mode and sensitive/case/noncanonical siblings. Its two effective pre-fix failures and five green tests are saved in the phase2 directory. See the phase2 report/delta for the current regression counts and new dependency pins.

The report below is retained as historical context and describes the earlier reviewed phase1 bytes only.

---

# 第一阶段实现报告（2026-10-21）

结论：工具独评r2已关闭R1/R2；本轮修复剩余R3的静态pickle校验，**等待本阶段第三轮独立复核；不是完整更新器，不具备本轮上线条件**。所有写入限定在新目录 `release-flow78-1021/`；未改产品worktree、旧部署脚本、生产或全局配置。没有真实产品打包、Docker build、pip/npm下载、云/浏览器操作、上传或真实凭据/备份读取。

## 已交付

- 固定40位commit/tree的Git对象白名单打包器；禁止从脏工作区采集，导出完整Alembic而非单个067文件。
- 严格pins及manifest结构：源码/依赖锁/迁移/dist来源/控制程序与模板/固定基础镜像；逐文件size/SHA、Git模式检查、路径/私密文件/常见密钥字面量拒绝、重复JSON键拒绝、源码和磁盘容量/文件数上限。
- 离线依赖规则、dist的生产origin及HTML本地资产引用校验；确定性tar.gz、独占创建、全成员完整读回。输出明确 `deployable=false`。
- 两个离线Dockerfile模板。backend包含app+完整Alembic/ini、明确067源码标识；两者默认拒绝业务启动，等待后续受审更新器覆盖入口。既有Python/Nginx基础image ID是强制固定值，实际服务器对应关系尚未检查。
- 新067启动器：明确角色、配置白名单与固定DB/Redis/origin、app与bypass角色flags、PG18.6、单一schema067及既有管理员检查；不执行迁移或seed。保留worker四队列、三项维护排除、七项业务任务和已有beat持久化证明。
- 独立显式迁移入口：使用当前env.py真正读取的asyncpg `DATABASE_URL_BYPASS`，先验owner/角色/066、持有部署advisory锁、再次验066，固定迁移067；正常等待180秒后进入退出清理，确认子进程结束才解锁，最后只接受唯一067。缺许可时不检查迁移树、不连接DB、更不跑Alembic。上级升级方案§4的“同步连接”措辞不准确，以此asyncpg入口为准。
- 未来更新器状态许可接口：绑定操作/源码/release hash/config hash/批准引用；迁移附新鲜备份与停写阶段，启动按api/worker/beat分别授权。仅消费root-owned 0600固定文件，**没有许可生成器**；状态声明不能替代真实停写/备份证据。
- README说明既有 `/opt/production` 运维挂载、backup固定API容器、renew固定Web名/旧Certbot metadata、beat健康检查等兼容约束，并列明后续必须补齐的状态机和验收。

## 实际本地验证

最终执行使用已有隔离CPython3.12环境：

| 检查 | 实际结果 |
|---|---|
| 原实现上的三项新负例 | 3 tests，3 failures，退出码1；均为业务断言红而非导入/环境错误 |
| 修复后的r1扩展回归（上一轮历史证据） | **15 tests，2.106秒，OK，退出码0；0 skip** |
| r2四项评审原例在修复前运行 | **4 tests，4 failures，0.056秒，退出码1**；均为GuardError未抛出，非导入错误 |
| 修复后的r2扩展回归 | **26 tests，0.444秒，OK，退出码0；0 skip** |
| `python -B -m unittest -v test_release_tools test_revision_r1 test_revision_r2` | **82 tests，18.155秒，OK，退出码0；0 skip** |
| `ruff check --isolated --select E4,E7,E9,F`（9个Python文件） | All checks passed，退出码0 |
| `ruff format --isolated --line-length 100 --check`（9个Python文件） | 9 files already formatted，退出码0 |
| JSON schema | 解析成功，生成manifest必要字段与Node版本正则合同断言通过；未安装/运行完整第三方Draft2020-12验证器 |

完整最终测试输出见 `test-results.txt`。实现/测试/模板/schema/README的固定size/SHA另见 `implementation-pins.json`，用于独立复核输入识别，不是生产制品pin。

主要行为证据：两次合成包字节SHA一致；所有manifest成员读回hash一致；真实Git对象中合法历史迁移全部入包；追踪的fixture `.env`/GitHub配置与测试代码不入包；脏工作区私钥内容不被读取。对新迁移head/断链、模板与控制代码漂移、未知基镜像、缺pin、错dist来源/实际origin、私密路径、Git symlink、路径穿越、重复JSON键、文件上限、离线锁直链/索引、wheel hash集合不符、外部/缺失HTML资产、同名大小写冲突、输出覆盖均实际获得拒绝。

运行入口验证使用模拟连接/进程：schema错则不启动；api许可不能启动worker；缺迁移许可不连接DB；普通app被授BYPASSRLS被拒；多个数据库head被拒；忙锁不拉起子进程；子进程成功/失败及超时都验证锁释放，固定目标067无`head`默认。这些是本地控制逻辑证据，**不是PG/RLS或Docker实跑证据**。

## 独评r1修复与新增证据

三个问题均接受，未以阶段边界反驳真实缺陷。修复前将独评案例写成回归，实际得到：R1事件只有lock/unlock、没有terminate；R2真实本地st_nlink=2仍可打包；R3只有.unrelated前缀文件仍调用Celery start。完整红灯输出保留 `revision-r1-red-results.txt`。

- **R1**：spawn任务和最终清理均有独立所有权。外层取消先等待取回子进程；清理受shield保护，terminate/必要时kill/wait确认退出完成后才解锁/close。新增三条次序回归：第一次wait取消；超时清理中连续两次取消并走kill；spawn尚未返回时取消。均断言exit_confirmed在unlock之前，并保留取消语义。只证明模拟子进程及协作式任务取消；不声称能在SIGKILL/主机关机中执行Python清理。
- **R2**：统一read_local的lstat与fstat均要求st_nlink=1及普通文件。三个真实本地硬链接负例覆盖dist、dependency、control；第四条模拟打开后出现硬链接的fstat竞态。前三项均在输出创建前拒绝，不读取真实秘密。
- **R3（r1时的部分修复）**：beat启动参数和root状态许可共同绑定明确DBM格式，不提供默认。GNU只接受主文件；dumb接受.dat/.dir及可选.bak。只读open、metadata存在、raw value完整读取、读前后文件元数据一致后才启动。新增备份孤件、缺.dat、无关前缀、损坏DB、未知/错格式、许可格式不符及真实合成dumb正例。r2指出原genops遍历仍放过栈/memo/FRAME/协议破损，该部分此前不能称静态合法性证明；本轮修复如下。

完整15条新增回归输出见 `revision-r1-green-results.txt`；其中GNU驱动未在Windows环境实跑。`implementation-pins.json`重新冻结实现/测试/说明与证据文件，不纳入独立review报告，保留其原始输入事实。`deployable=false`及候选默认拒绝启动保持不变。

## 独评r2剩余R3修复

接受该缺陷，没有用pickletools.dis替换后宣称完成。先在原实现上重现空栈STOP、未定义memo999、FRAME1000越界、PROTO255四例；均让Mock Celery启动，红灯输出完整保留 `revision-r2-red-results.txt`。

新增 `schedule_pickle_guard.py`，只使用genops词法解码，随后自行核PROTO4、完整FRAME边界、STOP最终栈、mark、memo、typed tuple/dict参数以及六个固定对象符号的静态描述。未知opcode/global/对象/参数明确拒绝，没有load/loads/Unpickler、动态import或编码函数调用。RAW最多1MiB，10000指令/遍历节点、深度32；循环和过度展开拒绝。该文件已加入控制白名单、manifest schema/pins与Dockerfile COPY；合成候选包读回测试明确要求包含它，manifest controls与schema一致，未依赖本机单独import冒充包完整性。

调度结构要求精确四metadata键、UTC/true、5.x.y版本、七项固定任务身份及八参数ScheduleEntry、标准库UTC datetime、非负计数、空args/kwargs、已知队列。支持正interval及有限crontab参数；详见README有限合同。空entries和缺项/多项现在拒绝。类型、构造arity、日历、时区、crontab范围/state、未知全局、协议3/5/255、中途PROTO、frame嵌套/跨操作数/提前结束/尾随字节、memo未定义/循环、栈下溢/剩余栈/缺mark及dict参数等均有合成负例。

正例使用既有隔离环境的 **Celery5.4.0** ScheduleEntry/schedule/crontab类，仅构造固定本地数据并用protocol4序列化；未启动任何Celery服务、调用任务或连接broker。样本实际含FRAME、MEMOIZE/BINGET、STACK_GLOBAL、REDUCE、BUILD；测试屏蔽pickle.load/loads/Unpickler后静态校验通过。完整dumb数据库读取前后全部文件SHA相同，Mock Celery仅调用一次。旧r1正例夹具也升级为真实类的七项合成样本，避免继续拿空dict冒充既有调度结构。

本轮26条扩展测试见 `revision-r2-green-results.txt`；82条总套件见 `test-results.txt`。这是有限支持结构的本地校验，不是通用pickle执行器或真实镜像兼容验收：ZoneInfo/pytz、自定义类、其他协议、合法但未支持的cron语法/参数等会停止；真实旧库若使用这些形式，须未来批准执行前只读核对和单独评估，不能自动放宽或重建。真实Linux/GNU驱动、部署镜像Python/Celery行为及生产业务均未验证。R1/R2关闭后的实现不再改动；不碰phase2计划。

## 未交付/未验证

1. 最终PR7+PR8的准确树、真实dist、真实候选包/服务器镜像；当前生产未改变。
2. 维护闸门、队列/ETA/retry/unacked排空、备份/恢复/切流/回退和timer互斥状态机。许可发行必须由该后续更新器实际验证条件，不能凭本阶段手写许可跳过。
3. Linux真实文件owner/0600/no-follow及mount保护、真实容器image ID、离线build、wheel安装及运行import、真实PG18.6迁移与角色验证；真实Linux DBM支持/旧调度库格式、GNU只读正例和Celery对象兼容仍待后续批准执行前核对。原验收的数据库存在布尔值不能证明格式。
4. 迁移阶段安全审计日志/实际映射回填行数采集。本入口暂丢弃Alembic子进程stdout/stderr避免秘密错误输出；后续更新器需补受保护/裁剪的结果与D6/K7/K8比较。
5. 业务权限/并发/FI和截图，仍由两PR自身验证及独评负责；本阶段不消耗产品最终独评轮次。
6. 完整第三方JSON-schema校验。当前核心门禁是Python语义验证器，README明示结构schema无法单独证明跨字段或实际内容一致。

本轮没有对外通知，没有新长期凭据、计费通道或存储迁移。保持两个PR一次维护窗口部署的既定目标；后续root可先独立复核本目录，再安排完整更新器阶段。
