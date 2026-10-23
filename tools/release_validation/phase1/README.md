# PR7 / PR8 杭州发布工具：第一阶段

这是本地可审工具候选，**不能据此上线**。未包含维护/队列/备份/恢复/切流状态机；没有生产许可文件生成器，没有最终产品SHA或真实发布包。默认生成manifest的 `deployable=false`，两个镜像模板默认都拒绝启动业务；必须等后续完整更新器、独立复核和用户已审生产方案。

## 本阶段文件

| 文件 | 作用 |
|---|---|
| `release_builder.py` | 固定Git commit/tree，白名单读取Git blob；验证dist/依赖/控制程序pin；生成确定性候选tar.gz并全文件读回 |
| `release-manifest.schema.json` | 候选manifest的Draft2020-12结构；跨字段/路径/哈希及源码语义仍由Python验证器检查 |
| `Dockerfile.backend.offline.in` | 固定既有Python标签，离线安装hash锁；复制完整app、Alembic树/ini及067工具；不自动迁移或启动 |
| `Dockerfile.frontend.offline.in` | 固定既有Nginx标签，只复制dist与release信息；默认拒绝启动，须后续更新器显式选受审HTTPS配置 |
| `runtime_guard.py` | 版本/受限数据库角色/固定目标/配置键白名单/许可文件检查；不打印配置值 |
| `start_067.py` | 明确api/worker/beat角色；只在数据库已是067且对应角色获许可后启动；保留业务队列、三维护排除、旧beat持久化证明 |
| `schedule_pickle_guard.py` | 仅识别明确限定的protocol4静态栈与Celery调度结构；不加载对象或调用编码函数，未知形式拒绝 |
| `migrate_067.py` | 独立执行066→067，需迁移许可；真实clothing_bypass校验、独占advisory锁、显式目标、180秒子进程界限；禁止降级/隐式head |
| `test_release_tools.py` | 合成Git仓库、dist、pin和模拟连接/子进程；不连接任何真实DB或执行Docker |
| `test_revision_r1.py` | 三项独评问题的取消/硬链接/只读调度库回归；仅模拟子进程与本地合成DBM |
| `test_revision_r2.py` / `schedule_test_fixture.py` | 损坏pickle回归及实际Celery类合成序列化样本；不启动Celery或连接broker，不反序列化 |

## 输入合同

Python最低3.12，本机已有Git。`--repo`只用于读取对象；工作区未提交修改不进包。`--commit`必须是明确40位小写SHA并等于pins；还要求明确40位tree。源码允许：`backend/app/`下py/json/sql、完整`backend/alembic/`下py/mako、`backend/alembic.ini`和`backend/requirements.txt`。前端package-lock只校验源hash，不复制源前端进运行包。其余路径忽略；允许树里的私密路径、密钥字面量、链接则拒绝。

pins必须完整提供schema/version、source/tree、066→067、准确origin、固定Python/Nginx既有runtime image ID、requirements/migration hash、wheel锁与全部wheel的size/SHA、dist的同源commit/lockhash/Node/npm版本/全部文件pin、四个控制程序及两个Dockerfile模板pin。拒绝额外字段、重复JSON键、缺pin、未知或错版本。公开构建来源/依赖闭包审核记录由编排者取得；`closure_reviewed=true`是显式输入证据要求，**不是本工具已经跑过pip安装或证明所有wheel可安装**。

`--dist`与`--dependencies`必须是独立目录，所有文件与pins精确相等，不允许额外文件、路径别名/链接、路径穿越、源码映射或私密文件。磁盘读取在打开前lstat和打开后fstat均要求普通文件且st_nlink=1；dist、依赖和控制程序共用此门禁，目录外硬链接也拒绝。dist必须含index.html及其本地静态引用，生产origin准确；禁止外部HTML资产、旧Zeabur/trial/localhost引用。Linux依赖锁只允许 `name==version --hash=sha256:...`，不接受索引地址、直链、editable或依赖下载指令；锁hash集合须与wheel文件hash集合一致。不会执行pip/npm或读取原配置。

包输出必须是新的明确路径；不覆盖、不创建缺失父目录。所有验证后才独占创建；异常中断留下的文件不得视为成功包，后续不能自动覆盖重试。tar无符号/硬链接和非普通成员，manifest与所有内容读回一致后才返回 `PHASE1_CANDIDATE_VERIFIED`；仍明确 `deployable=false`。

本阶段没有真实产品pins示例；测试方法内部创建合成pins，避免将当前未冻结PR head误当最终生产输入。包生成器命令接口为：

```text
python release_builder.py --repo <fixed-repo> --commit <40-lowercase-sha> --pins <reviewed-public-pins.json> --dist <independent-dist> --dependencies <verified-linux-dependencies> --output <new-candidate.tar.gz>
```

该接口本轮只由合成测试调用；不要据此打包当前产品分支。

## 运行入口与后续更新器接缝

两个入口的构造均要求明确 `--expected-commit`。start另要求api/worker/beat参数；beat必须提供已核实的 `--beat-db-format`（当前工具仅支持dbm.gnu/dbm.dumb），api/worker不得带此项。它们不读取 `.env`，只允许固定 `/run/production/runtime-config.json`（root-owned、0600、单链接普通文件），字段为原部署已保留项与固定运行项；拒绝PATH/PYTHONPATH等注入字段，清理继承环境，再载入受审配置。

未来更新器需分别生成并只读挂载 `/run/release-guard/start-permit.json`、`/run/release-guard/migrate-permit.json`，同样要求root-owned/0600/单链接。**这是可信更新器持有的状态文件，不是密码、数字签名或自动获得用户批准的机制。** 许可中的approval_ref仅引用外部已取得的批准；本工具不签发许可、不推定批准、不提供制作许可命令。

许可绑定操作、commit、release.json SHA、现配置SHA和批准引用；迁移许可还绑定新鲜备份SHA、066/067与 `backup_verified_writes_paused` 阶段；启动许可列出允许角色，api许可不允许worker/beat。启动许可新增必填 `beat_db_format`：含beat角色必须填已核实的受支持格式，否则必须为null；beat启动参数须与许可一致。未来更新器必须先实际证明停写、排空、新鲜备份读回和迁移等条件，不能只手写这些字段宣称已过门。

迁移使用当前Alembic `env.py`真正读取的 `DATABASE_URL_BYPASS`（asyncpg）；同步URL不是此次Alembic入口，上级升级方案§4的“同步连接”措辞应以此为准。先校验PG18.6、固定库/owner、NOSUPERUSER且BYPASSRLS，再获得固定advisory锁并重验066；启动明确 `python -m alembic ... upgrade 067_retro_completion`。统一子进程所有权清理覆盖超时、异常和任务取消；spawn本身也屏蔽调用任务取消以便取回句柄。清理先terminate，5秒未退出则kill并wait确认，再解锁/关闭持锁连接；清理期间重复取消只延后传播，不中断该顺序。成功后再校验唯一067。该锁只协调遵守协议的发布程序，不等于阻断业务写入，所以维护/队列状态机仍是必需部分。

迁移子进程stdout/stderr当前丢弃，避免库连接错误泄露秘密；只返回安全错误码。**迁移回填行数与完整生产审计日志尚未在本阶段落盘**，后续更新器须增加受保护、安全裁剪的观测机制及K7/K8基线比较，不能声称现入口已完成整套生产迁移验收。180秒是正常等待阈值，不是整轮强制时限或SQL逐语句超时；kill后仍须确认退出才能解锁，不能用另一个超时直接释放锁。此保护面向协作式任务取消，不保证进程被SIGKILL、解释器崩溃或主机关机时仍能清理；这些场景须在后续更新器的持久化断点/现场核查处理。

## 必须保留的现运维约束

- 后续更新器必须在build前验证固定本地标签解析到manifest中的原image ID；`--pull=false --network=none`，不能只看标签。第一阶段不调用Docker，因此**没有证明服务器已有镜像仍匹配**。
- DB/Redis保持原镜像、数据卷和网络；应用四容器名保持不变。API/worker/beat统一新版后端image ID；worker关闭继承的API健康检查，beat继续独立健康检查。
- 原backup通过固定API容器中的 `/opt/production/upload_backup.py` 上传，原renew从旧生产root的 `built-images.json` 取Certbot ID并按固定Web名reload。新工具装在 `/opt/release`，没有覆盖原 `/opt/production`。未来compose须保留原受审运维目录只读挂载，不能拿本阶段control目录整体替代它。
- 旧Certbot metadata、backup/renew脚本、证书目录与timer保留；不重跑prepare/restore/deploy/certificate/activate。包里不含这些旧控制文件或其秘密输入；不会把一个新built-images.json覆盖旧文件。
- 新beat须读到原scheduler数据库和原proof。工具按明确格式检查准确文件布局：GNU单一主文件，dumb必须同时有.dat/.dir、可额外有.bak；其他前缀残片、备份孤件、链接或格式不符均拒绝。只用指定DBM驱动的`r`模式打开，逐项读取raw bytes，执行下述有限静态合同，并核读前后文件身份/内容元数据不变。不会创建、重置、删除或重建调度库。
- 当前证据只证明旧beat调度库曾验收存在，不证明其DBM格式。真实Linux镜像支持和旧库格式仍待未来批准执行前只读核对；不能默认旧库是GNU或dumb。合成正例验证了dumb，Linux GNU及Celery对象实际兼容未实测。任务排空、worker唯一、beat唯一、缓存TTL、维护开关、真实备份/恢复、切流与回退均在后续阶段补齐。

## 验证边界

运行完整合成套件：`python -B -m unittest -v test_release_tools test_revision_r1 test_revision_r2`。测试生成的repo/wheel/dist/DBM都是夹具，其中wheel明确不可安装；只用于证明哈希/路径/打包行为，不模拟pip成功。临时目录由测试隔离并清理，无真实产品包落地。独评r1三项负例的有效红灯保留在 `revision-r1-red-results.txt`，扩展回归绿灯见 `revision-r1-green-results.txt`；r2四项实际红灯和26条扩展回归另见 `revision-r2-red-results.txt`、`revision-r2-green-results.txt`。

### 调度pickle的有限支持合同

`genops`仅负责词法解码，不再据此宣布输入合法。后续识别器显式要求开头单一PROTO4、末尾唯一结果STOP、完整且对齐指令边界的FRAME、已定义memo、正确的栈/mark及有类型的操作参数。只支持小型标量、tuple/dict、MEMOIZE/BINGET/LONG_BINGET，以及六个固定符号的惰性描述；GLOBAL旧编码、POP、任意导入/调用、NEWOBJ/扩展码/持久ID/外部buffer及其他未知opcode均拒绝。每值最多1MiB、10000指令/遍历节点、深度32，循环引用、重复dict键及扩张过大拒绝。

DBM必须恰有entries、__version__、tz、utc_enabled四键。版本只接受5.x.y字符串；UTC与true必须精确匹配。entries必须恰有固定清单中的七项，每项均是八参数ScheduleEntry描述：身份匹配，合法UTC日期（datetime.datetime二参数字节编码、datetime.timezone零offset；不支持ZoneInfo/pytz或fold），非负运行计数，空args/kwargs，options只含四种既有队列之一。schedule只支持正规范化timedelta、relative=False、nowfun=None的interval；或五字段crontab，字段仅有效整数/纯数字字符串/`*`/有界`*/n`，state为缺省或空dict。crontab名称、范围、列表、自定义state/nowfun和其他合法但未知形式也拒绝。

识别器不导入pickle指定的符号，不调用REDUCE或BUILD编码的函数，不使用load/loads/Unpickler；日期范围检查只调用工具源码固定的标准库date。正例由既有隔离环境实际Celery类构造并序列化，含interval/crontab/UTC与memo/FRAME；未反序列化合成或真实对象。**有限静态合同不等于通用pickle可加载证明，也不等于真实部署镜像中的Celery兼容验收。** 原库格式、Python/Celery版本、ZoneInfo等真实表示尚未核对；未知则停止，不重建库、不改格式、不自动放宽。未来批准执行前须先核这些兼容条件，不能为绕过门禁重置调度状态。

JSON schema已解析并对生成manifest必要字段/正则做合同检查；本环境没有jsonschema库，未安装，也未声称运行完整Draft2020-12第三方验证器。实际生成门禁使用本地Python验证器，并验证跨字段/真实Git对象/磁盘内容；schema不能替代这些验证。

安全路径/凭据字面量检查是明确的拒绝规则，不是通用秘密扫描器。源码白名单、固定Git对象、公开pin审查和独立复核仍不可省略。最终前端产物若命中词法拒绝，须定位和审查，不能静默放宽规则。

还未验证：Linux容器实际运行、Dockerfile实际build、wheel安装、Alembic真实PG、生产root文件权限/挂载、真实角色/RLS、现timer兼容、备份恢复、维护/队列/回退状态机、产品最终SHA与端到端生产验收。本阶段不能作为合并或上线通过证据。
