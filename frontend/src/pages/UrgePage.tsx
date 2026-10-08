import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Drawer,
  Empty,
  Form,
  Image,
  Input,
  InputNumber,
  Modal,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  message,
} from "antd";
import { SettingOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import {
  closeUrgeTask,
  getUrgeConfig,
  getUrgeDashboard,
  getUrgeTask,
  listUrgeTasks,
  updateUrgeConfig,
  urgeBatch,
} from "@/features/urge/api";
import type {
  UrgeConfig,
  UrgeTask,
  UrgeTaskFilters,
  UrgeTaskStatus,
} from "@/features/urge/types";
import { extractErrorMessage } from "@/services/apiClient";
import { useAuthStore } from "@/stores/authStore";
import { BloggerHoverCard } from "@/components/BloggerHoverCard/BloggerHoverCard";
import { DisplayNameCell } from "@/components/DisplayNameCell/DisplayNameCell";
import { StyleSelect } from "@/components/RemoteSelect/StyleSelect";
import { UrgeModal } from "@/components/UrgeModal/UrgeModal";

const statusColor: Record<UrgeTaskStatus, string> = {
  进行中: "processing",
  已关闭: "default",
};

const closeReasonColor: Record<string, string> = {
  博主已发布: "green",
  已取消: "orange",
  手动关闭: "default",
};

/**
 * 催发任务（PRD V1.4 改动 2）。
 *
 * 一个推广单一个任务，一个任务多条催发留痕（截图 + 时间 + 备注，时间线倒序）。
 * 博主发文或单据取消时任务自动关闭。
 *
 * 阈值（临期多少天开始催、催几次提示主管）在右上角配置里，权限是独立一级域
 * urge_config —— PR 看得到催发任务但改不了阈值。
 */
export function UrgePage() {
  const qc = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const canConfig = Boolean(
    user?.roles.some((r) => ["admin", "platform_admin", "pr_manager"].includes(r))
  );

  const [filters, setFilters] = useState<UrgeTaskFilters>({
    status: "进行中",
    page: 1,
    page_size: 20,
  });
  const [detailId, setDetailId] = useState<string | null>(null);
  // 催发弹窗（与推广页共用 UrgeModal，可附截图）
  const [urgeTarget, setUrgeTarget] = useState<UrgeTask | null>(null);
  const [configOpen, setConfigOpen] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const [configForm] = Form.useForm<UrgeConfig>();
  const [batchForm] = Form.useForm<{ style_id: string; note?: string }>();

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["urge-tasks"] });
    void qc.invalidateQueries({ queryKey: ["urge-dashboard"] });
  };

  const { data: board } = useQuery({
    queryKey: ["urge-dashboard"],
    queryFn: getUrgeDashboard,
  });

  const { data, isLoading } = useQuery({
    queryKey: ["urge-tasks", filters],
    queryFn: () => listUrgeTasks(filters),
  });

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ["urge-task", detailId],
    queryFn: () => getUrgeTask(detailId as string),
    enabled: !!detailId,
  });

  const closeMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      closeUrgeTask(id, reason),
    onSuccess: (d) => {
      message.success("任务已关闭");
      invalidate();
      void qc.invalidateQueries({ queryKey: ["urge-task", d.id] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const configMutation = useMutation({
    mutationFn: updateUrgeConfig,
    onSuccess: () => {
      message.success("阈值已保存");
      setConfigOpen(false);
      invalidate();
      void qc.invalidateQueries({ queryKey: ["urge-config"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const batchMutation = useMutation({
    mutationFn: (v: { style_id: string; note?: string }) =>
      urgeBatch(v.style_id, v.note),
    onSuccess: (r) => {
      message.success(
        r.skipped_closed > 0
          ? `已催发 ${r.urged_count} 单，跳过 ${r.skipped_closed} 单（任务已关闭）`
          : `已催发 ${r.urged_count} 单`
      );
      setBatchOpen(false);
      batchForm.resetFields();
      invalidate();
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  async function openConfig() {
    try {
      const cfg = await getUrgeConfig();
      configForm.setFieldsValue(cfg);
      setConfigOpen(true);
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  }

  function confirmClose(task: UrgeTask) {
    let reason = "";
    Modal.confirm({
      title: "关闭催发任务",
      content: (
        <div>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
            关闭后不再催这一单。已经决定走召回或转取消时用。
          </Typography.Paragraph>
          <Input.TextArea
            rows={3}
            placeholder="关闭原因（可选，会写进催发时间线）"
            onChange={(e) => {
              reason = e.target.value;
            }}
          />
        </div>
      ),
      okText: "确认关闭",
      cancelText: "取消",
      onOk: () => closeMutation.mutateAsync({ id: task.id, reason }),
    });
  }

  const columns: ColumnsType<UrgeTask> = [
    {
      title: "推广单",
      dataIndex: "promotion_internal_code",
      width: 150,
      render: (v: string | null) => v || "—",
    },
    {
      title: "博主",
      dataIndex: "blogger_nickname",
      width: 140,
      render: (v: string | null, r) =>
        v ? (
          <BloggerHoverCard bloggerId={r.blogger_id} bloggerName={v}>
            {v}
          </BloggerHoverCard>
        ) : (
          "—"
        ),
    },
    {
      title: "款式",
      dataIndex: "style_code",
      width: 150,
      render: (v: string | null, r) => {
        // 第二行是品名：商品简称，没填回落建单快照（7a-8）
        const name = r.display_short_name ?? r.style_name;
        return (
          <span>
            <div>{v || "—"}</div>
            {name && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                <DisplayNameCell name={name} fullTitle={r.goods_title} />
              </Typography.Text>
            )}
          </span>
        );
      },
    },
    { title: "PR", dataIndex: "pr_name", width: 100, render: (v) => v || "—" },
    {
      title: "预定发布",
      dataIndex: "scheduled_publish_date",
      width: 150,
      render: (v: string | null, r) => (
        <Space size={4}>
          <span>{v || "未排期"}</span>
          {r.overdue_days != null && (
            <Tag color="red">超期 {r.overdue_days} 天</Tag>
          )}
        </Space>
      ),
    },
    {
      title: "已催",
      dataIndex: "urge_count",
      width: 110,
      align: "right",
      render: (v: number, r) =>
        r.over_limit ? (
          <Tooltip
            title={`已超过 ${board?.max_urge_times ?? 3} 次，考虑召回或转取消`}
          >
            <Tag color="red">{v} 次</Tag>
          </Tooltip>
        ) : (
          <span>{v} 次</span>
        ),
    },
    {
      title: "最近催发",
      dataIndex: "last_urged_at",
      width: 150,
      render: (v: string | null) =>
        v ? dayjs(v).format("YYYY-MM-DD HH:mm") : "—",
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 120,
      render: (v: UrgeTaskStatus, r) => (
        <Space size={4} direction="vertical" align="start">
          <Tag color={statusColor[v]}>{v}</Tag>
          {r.close_reason && (
            <Tag color={closeReasonColor[r.close_reason]}>{r.close_reason}</Tag>
          )}
        </Space>
      ),
    },
    {
      title: "操作",
      width: 170,
      fixed: "right",
      render: (_, r) => (
        <Space>
          <Button type="link" size="small" onClick={() => setDetailId(r.id)}>
            时间线
          </Button>
          <Button
            type="link"
            size="small"
            disabled={r.status !== "进行中"}
            onClick={() => setUrgeTarget(r)}
          >
            催发
          </Button>
          <Button
            type="link"
            size="small"
            danger
            disabled={r.status !== "进行中"}
            onClick={() => confirmClose(r)}
          >
            关闭
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Row gutter={16}>
        <Col xs={12} md={6}>
          <Card>
            <Statistic
              title={
                board?.week_start
                  ? `本周已催发（${board.week_start} 起）`
                  : "本周已催发"
              }
              value={board?.urged_this_week ?? 0}
              suffix="次"
            />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card>
            <Statistic title="待催发" value={board?.pending ?? 0} suffix="单" />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card>
            <Statistic
              title="超时未回"
              value={board?.overdue ?? 0}
              suffix="单"
              valueStyle={{ color: (board?.overdue ?? 0) > 0 ? "#cf1322" : undefined }}
            />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card>
            <Statistic
              title={`催过 ${board?.max_urge_times ?? 3} 次`}
              value={board?.over_limit ?? 0}
              suffix="单"
              valueStyle={{
                color: (board?.over_limit ?? 0) > 0 ? "#d46b08" : undefined,
              }}
            />
          </Card>
        </Col>
      </Row>

      {board && !board.auto_scan_enabled && (
        <Alert
          type="warning"
          showIcon
          message="自动催发已关闭"
          description="当前只能手动催发，临期的单据不会自动建任务。要恢复请到右上角的催发设置里打开。"
        />
      )}

      <Card
        title={
          <Typography.Title level={4} style={{ margin: 0 }}>
            催发任务
          </Typography.Title>
        }
        extra={
          <Space>
            <Button onClick={() => setBatchOpen(true)}>按款式批量催发</Button>
            {canConfig && (
              <Button icon={<SettingOutlined />} onClick={() => void openConfig()}>
                催发设置
              </Button>
            )}
          </Space>
        }
      >
        <Space style={{ marginBottom: 16 }} wrap>
          <Input.Search
            placeholder="搜博主 / 推广单编码 / 款号"
            allowClear
            style={{ width: 240 }}
            onSearch={(v) =>
              setFilters((f) => ({ ...f, keyword: v || undefined, page: 1 }))
            }
          />
          <Select<UrgeTaskStatus | "all">
            value={filters.status ?? "all"}
            style={{ width: 120 }}
            options={[
              { label: "全部状态", value: "all" },
              { label: "进行中", value: "进行中" },
              { label: "已关闭", value: "已关闭" },
            ]}
            onChange={(v) =>
              setFilters((f) => ({
                ...f,
                status: v === "all" ? undefined : v,
                page: 1,
              }))
            }
          />
          <Select<string>
            value={
              filters.overdue_only
                ? "overdue"
                : filters.over_limit_only
                  ? "over_limit"
                  : "all"
            }
            style={{ width: 150 }}
            options={[
              { label: "不限", value: "all" },
              { label: "只看超时", value: "overdue" },
              { label: "只看催过头", value: "over_limit" },
            ]}
            onChange={(v) =>
              setFilters((f) => ({
                ...f,
                overdue_only: v === "overdue" || undefined,
                over_limit_only: v === "over_limit" || undefined,
                page: 1,
              }))
            }
          />
        </Space>

        <Table
          rowKey="id"
          loading={isLoading}
          columns={columns}
          dataSource={data?.items ?? []}
          scroll={{ x: 1400 }}
          pagination={{
            current: data?.page ?? 1,
            pageSize: data?.page_size ?? 20,
            total: data?.total ?? 0,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (page, page_size) =>
              setFilters((f) => ({ ...f, page, page_size })),
          }}
        />
      </Card>

      <Drawer
        title="催发时间线"
        width={520}
        open={!!detailId}
        onClose={() => setDetailId(null)}
        destroyOnHidden
      >
        {detailLoading ? (
          <Typography.Text type="secondary">加载中…</Typography.Text>
        ) : !detail ? (
          <Empty description="没有数据" />
        ) : (
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            <Space direction="vertical" size={2}>
              <Typography.Text strong>
                {detail.promotion_internal_code} · {detail.blogger_nickname}
              </Typography.Text>
              <Typography.Text type="secondary">
                {detail.style_code}
                {(detail.display_short_name ?? detail.style_name) && (
                  <>
                    {" "}
                    <DisplayNameCell
                      name={detail.display_short_name ?? detail.style_name}
                      fullTitle={detail.goods_title}
                    />
                  </>
                )}{" "}
                ·{" "}
                {detail.scheduled_publish_date
                  ? `预定 ${detail.scheduled_publish_date}`
                  : "未排期"}
              </Typography.Text>
              <Space size={4}>
                <Tag color={statusColor[detail.status]}>{detail.status}</Tag>
                {detail.close_reason && (
                  <Tag color={closeReasonColor[detail.close_reason]}>
                    {detail.close_reason}
                  </Tag>
                )}
                <Tag>已催 {detail.urge_count} 次</Tag>
              </Space>
            </Space>

            {detail.records.length === 0 ? (
              <Empty description="还没有催发记录" />
            ) : (
              <Timeline
                items={detail.records.map((r) => ({
                  color: r.trigger_type === "自动" ? "blue" : "green",
                  children: (
                    <Space direction="vertical" size={4}>
                      <Space size={6}>
                        <Tag color={r.trigger_type === "自动" ? "blue" : "green"}>
                          {r.trigger_type}
                        </Tag>
                        <Typography.Text type="secondary">
                          {dayjs(r.created_at).format("YYYY-MM-DD HH:mm")}
                        </Typography.Text>
                        {r.created_by_name && (
                          <Typography.Text type="secondary">
                            {r.created_by_name}
                          </Typography.Text>
                        )}
                      </Space>
                      {r.note && <Typography.Text>{r.note}</Typography.Text>}
                      {r.screenshot_url && (
                        <Image
                          src={r.screenshot_url}
                          alt="催发截图"
                          width={160}
                          style={{ borderRadius: 4, border: "1px solid #f0f0f0" }}
                        />
                      )}
                    </Space>
                  ),
                }))}
              />
            )}
          </Space>
        )}
      </Drawer>

      <UrgeModal
        open={!!urgeTarget}
        promotionId={urgeTarget?.promotion_id ?? null}
        title={
          urgeTarget
            ? `催发 · ${urgeTarget.promotion_internal_code ?? "—"} · ${urgeTarget.blogger_nickname ?? "—"}`
            : "催发"
        }
        onClose={() => setUrgeTarget(null)}
      />

      <Modal
        title="催发设置"
        open={configOpen}
        onCancel={() => setConfigOpen(false)}
        onOk={() => configForm.submit()}
        confirmLoading={configMutation.isPending}
        destroyOnHidden
        width={520}
      >
        <Form
          form={configForm}
          layout="vertical"
          onFinish={(v) => configMutation.mutate(v)}
          style={{ marginTop: 16 }}
        >
          <Form.Item
            name="no_publish_days"
            label="临期多少天开始自动催"
            extra="距预定发布日还剩这么多天且仍未发布，每天自动催一次"
            rules={[{ required: true, message: "请填写天数" }]}
          >
            <InputNumber min={1} max={60} style={{ width: "100%" }} addonAfter="天" />
          </Form.Item>
          <Form.Item
            name="max_urge_times"
            label="催几次后提示主管"
            extra="超过这个次数的单据会标红，主管据此决定要不要召回或转取消。这是提示阈值，不是硬上限"
            rules={[{ required: true, message: "请填写次数" }]}
          >
            <InputNumber min={1} max={20} style={{ width: "100%" }} addonAfter="次" />
          </Form.Item>
          <Form.Item
            name="max_overdue_days"
            label="超期多少天后停止自动催"
            extra="超过这个天数的陈旧单据不再自动建任务（仍可手动催）。历史导入的旧排期全靠这条挡住，不然会一次性建出几千个任务"
            rules={[{ required: true, message: "请填写天数" }]}
          >
            <InputNumber min={1} max={365} style={{ width: "100%" }} addonAfter="天" />
          </Form.Item>
          <Form.Item
            name="urge_threshold_days"
            label="「催发」标签阈值"
            extra="推广列表的催发状态标签：距预定发布日 ≤ 这个天数显示「催发」"
            rules={[{ required: true, message: "请填写天数" }]}
          >
            <InputNumber min={1} max={60} style={{ width: "100%" }} addonAfter="天" />
          </Form.Item>
          <Form.Item
            name="important_threshold_days"
            label="「重要催发」标签阈值"
            extra="必须小于等于上面那个，否则重要催发永远取不到"
            rules={[{ required: true, message: "请填写天数" }]}
          >
            <InputNumber min={0} max={60} style={{ width: "100%" }} addonAfter="天" />
          </Form.Item>
          <Form.Item
            name="auto_scan_enabled"
            label="启用自动催发"
            valuePropName="checked"
            extra="关掉之后只能手动催，排查问题或业务暂停时用"
          >
            <Switch />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="按款式批量催发"
        open={batchOpen}
        onCancel={() => setBatchOpen(false)}
        onOk={() => batchForm.submit()}
        confirmLoading={batchMutation.isPending}
        destroyOnHidden
        width={520}
      >
        <Typography.Paragraph type="secondary">
          会给该款式下所有「未发布 / 异常」的推广单各催一次。批量不支持截图 ——
          一次催十几个博主贴同一张图没有留痕价值，要截图请逐条催。
        </Typography.Paragraph>
        <Form
          form={batchForm}
          layout="vertical"
          onFinish={(v) => batchMutation.mutate(v)}
        >
          <Form.Item
            name="style_id"
            label="款式"
            rules={[{ required: true, message: "请选择款式" }]}
          >
            <StyleSelect placeholder="搜索款号 / 款名" />
          </Form.Item>
          <Form.Item name="note" label="备注">
            <Input.TextArea rows={2} placeholder="这轮催发的说明（可选）" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
