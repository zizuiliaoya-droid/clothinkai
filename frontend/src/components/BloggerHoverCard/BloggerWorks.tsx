import { useState } from "react";
import { Alert, Button, Collapse, Form, Input, Modal, Space, Table, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ImportUploadButton } from "@/components/ImportUploadButton";
import { getBlogger } from "@/features/blogger/api";
import { getWorkSource, listWorks, saveWork } from "@/features/blogger/worksApi";
import type { WorkInput, WorkItem } from "@/features/blogger/worksApi";
import { extractErrorMessage } from "@/services/apiClient";
import { useReadOnlySupport } from "@/stores/supportStore";

const metrics = [
  ["like_count", "点赞"], ["comment_count", "评论"], ["share_count", "分享"],
  ["collect_count", "收藏"], ["play_count", "播放量"],
] as const;
const numberText = (value: unknown) => value == null || value === "" ? "—" : String(value);
const ratioText = (value: number | null) => value == null ? "—" : `${(value * 100).toFixed(2)}%`;
const timeText = (value: string) => new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });

const metricColumns: ColumnsType<WorkItem> = [
  ...metrics.map(([dataIndex, title]) => ({ title, dataIndex, width: 85, render: numberText })),
  { title: "互动总量", dataIndex: "interaction_total", width: 95, render: numberText },
  { title: "收藏/点赞", dataIndex: "collect_like_ratio", width: 100, render: ratioText },
  { title: "分享/点赞", dataIndex: "share_like_ratio", width: 100, render: ratioText },
  { title: "评论/点赞", dataIndex: "comment_like_ratio", width: 100, render: ratioText },
];

function WorkHistory({ bloggerId, snapshotId }: { bloggerId: string; snapshotId: string }) {
  const [page, setPage] = useState(1);
  const query = useQuery({ queryKey: ["blogger-works", bloggerId, "history", snapshotId, page], queryFn: () => listWorks(bloggerId, { snapshot_id: snapshotId, page }) });
  const openSource = async (row: WorkItem) => {
    try {
      const result = await getWorkSource(bloggerId, row.id);
      window.open(result.url, "_blank", "noopener,noreferrer");
    } catch (error) {
      message.error(extractErrorMessage(error, "原文件暂时无法打开"));
    }
  };
  if (query.isError) return <Alert type="error" message="采集记录加载失败" action={<Button onClick={() => void query.refetch()}>重试</Button>} />;
  return <Table size="small" rowKey="id" loading={query.isLoading} dataSource={query.data?.items ?? []}
    scroll={{ x: 1500 }} pagination={{ current: page, total: query.data?.total, pageSize: 10, showSizeChanger: false, onChange: setPage }}
    columns={[
      { title: "采集时间（北京时间）", dataIndex: "collected_at", width: 180, render: timeText },
      { title: "来源", dataIndex: "source", width: 150 },
      { title: "操作人", dataIndex: "created_by_name", width: 90, render: numberText },
      { title: "导入记录", width: 160, render: (_, row) => row.import_batch_id ? <Space direction="vertical" size={0}>
        <Typography.Text copyable={{ text: row.import_batch_id }} title={row.import_batch_id}>批次 {row.import_batch_id.slice(0, 8)}</Typography.Text>
        <span>数据行 {row.row_number}</span>
        {query.data?.can_read_source && <Button type="link" size="small" onClick={() => void openSource(row)}>原文件</Button>}
      </Space> : "手工补录" },
      ...metricColumns,
    ]} />;
}

export function BloggerWorks({ bloggerId, enabled }: { bloggerId: string; enabled: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState(false);
  const [form] = Form.useForm<WorkInput>();
  const readOnly = useReadOnlySupport();
  const qc = useQueryClient();
  const basic = useQuery({ queryKey: ["blogger-card-basic", bloggerId], queryFn: () => getBlogger(bloggerId), enabled, staleTime: 60_000 });
  const query = useQuery({
    queryKey: ["blogger-works", bloggerId, dateFrom, dateTo, page],
    queryFn: () => listWorks(bloggerId, { page, date_from: dateFrom || undefined, date_to: dateTo || undefined }),
    enabled: enabled && expanded,
  });
  const save = useMutation({
    mutationFn: (values: WorkInput) => saveWork(bloggerId, values),
    onSuccess: (result) => {
      message.success(result.inserted ? "采集记录已保存" : "已有相同采集记录，未重复添加");
      setEditing(false); form.resetFields();
      void qc.invalidateQueries({ queryKey: ["blogger-works", bloggerId] });
    },
    onError: (error) => message.error(extractErrorMessage(error, "保存失败")),
  });
  const columns: ColumnsType<WorkItem> = [
    { title: "发布日期", dataIndex: "published_on", width: 110 },
    { title: "作品", width: 200, render: (_, row) => <Space direction="vertical" size={0}>
      {row.work_url ? <a href={row.work_url} target="_blank" rel="noopener noreferrer">{row.title || row.work_id || "打开作品"}</a> : <span>{row.title || row.work_id}</span>}
      {row.work_id && <Typography.Text type="secondary">ID：{row.work_id}</Typography.Text>}
    </Space> },
    ...metricColumns,
    { title: "采集时间（北京时间）", dataIndex: "collected_at", width: 180, render: timeText },
    { title: "来源", dataIndex: "source", width: 150 },
  ];
  return <>
    {basic.data && <Space wrap style={{ marginBottom: 8 }}>
      <Typography.Text>{basic.data.platform} · {basic.data.xiaohongshu_id}</Typography.Text>
      <Typography.Text type="secondary">粉丝 {numberText(basic.data.follower_count)}</Typography.Text>
      {basic.data.homepage_url && <a href={basic.data.homepage_url} target="_blank" rel="noopener noreferrer">打开主页</a>}
    </Space>}
    <Collapse size="small" onChange={(keys) => setExpanded(keys.length > 0)} items={[{ key: "works", label: "作品与采集记录", children: <>
      <Space wrap style={{ marginBottom: 8 }}>
        <label>发布起日<Input aria-label="发布起日" type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPage(1); }} /></label>
        <label>发布止日<Input aria-label="发布止日" type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setPage(1); }} /></label>
      </Space>
      <Typography.Paragraph type="secondary">按发布时间倒序，每件作品显示最新采集值。展开作品查看历史记录。仅统计已采集作品，不代表账号全部作品。</Typography.Paragraph>
      {!readOnly && query.data?.can_write && <Space wrap style={{ marginBottom: 8 }}>
        <Button size="small" onClick={() => setEditing(true)}>补录作品</Button>
        <ImportUploadButton source="manual_blogger_work" label="导入作品" size="small" invalidateKeys={[["blogger-works"]]} successHint="请在导入记录查看完成结果，再刷新作品。" />
      </Space>}
      <Button size="small" type="link" onClick={() => void query.refetch()}>刷新作品</Button>
      {query.isError ? <Alert type="error" message={extractErrorMessage(query.error, "作品加载失败")} /> : <Table<WorkItem>
        size="small" rowKey="id" loading={query.isLoading} columns={columns} dataSource={query.data?.items ?? []}
        locale={{ emptyText: "暂无作品采集数据" }} scroll={{ x: 1600 }}
        pagination={{ current: page, total: query.data?.total, pageSize: 10, showSizeChanger: false, onChange: setPage, showTotal: (total) => `共 ${total} 件作品` }}
        expandable={{ expandedRowRender: (row) => <WorkHistory bloggerId={bloggerId} snapshotId={row.id} /> }}
      />}
    </> }]} />
    <Modal title="补录作品采集记录" open={editing} onCancel={() => setEditing(false)} onOk={() => form.submit()} confirmLoading={save.isPending} width={600}>
      <Typography.Paragraph type="secondary">作品 ID 与稳定链接至少填一项；同一作品后续采集保持相同标识方式。计数可填 14.3w，未采到留空。</Typography.Paragraph>
      <Form form={form} layout="vertical" onFinish={(values) => save.mutate(values)}>
        <Form.Item name="work_id" label="作品 ID" rules={[{ validator: async (_, value) => { if (!value && !form.getFieldValue("work_url")) throw new Error("作品 ID 或稳定链接至少填写一项"); } }]} dependencies={["work_url"]}><Input maxLength={128} /></Form.Item>
        <Form.Item name="work_url" label="稳定作品链接"><Input maxLength={2048} placeholder="https://…" /></Form.Item>
        <Form.Item name="title" label="标题"><Input maxLength={500} /></Form.Item>
        <Space align="start">
          <Form.Item name="published_on" label="发布日期" rules={[{ required: true }]}><Input type="date" /></Form.Item>
          <Form.Item name="collected_at" label="采集时间（北京时间）" rules={[{ required: true }]}><Input type="datetime-local" step={1} /></Form.Item>
        </Space>
        <Form.Item name="source" label="采集来源" rules={[{ required: true }]}><Input maxLength={128} placeholder="例如：平台作品页人工采集" /></Form.Item>
        <Space wrap>{metrics.map(([name, label]) => <Form.Item key={name} name={name} label={label}><Input style={{ width: 100 }} /></Form.Item>)}</Space>
      </Form>
    </Modal>
  </>;
}
