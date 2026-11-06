import { useState } from "react";
import { Alert, Button, Collapse, Form, Input, Modal, Select, Space, Switch, Table, Tag, Typography, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BarChart } from "@/components/Charts/BarChart";
import { ImportUploadButton } from "@/components/ImportUploadButton";
import { audienceDimensions, getAudienceSource, listAudience, saveAudience } from "@/features/blogger/audienceApi";
import type { AudienceInput, AudienceItem } from "@/features/blogger/audienceApi";
import { extractErrorMessage } from "@/services/apiClient";
import { useReadOnlySupport } from "@/stores/supportStore";

const text = (value: unknown) => value == null || value === "" ? "—" : String(value);
const percent = (value: unknown) => value == null || value === "" ? "—" : `${value}%`;

export function AudienceDistribution({ item }: { item: AudienceItem }) {
  const ranked = [...item.entries].sort((a, b) => Number(b.percentage ?? -1) - Number(a.percentage ?? -1));
  return <>
    <Space wrap>
      <Typography.Text>{item.collected_on} · {item.source}</Typography.Text>
      <Tag>{item.completeness === "partial" ? "部分数据" : "完整分布"}</Tag>
      {item.estimated && <Tag>估计值</Tag>}
      {item.is_current && <Tag color="green">当前</Tag>}
    </Space>
    {item.completeness === "partial" && <Typography.Paragraph type="secondary">仅展示已提供分类，未补齐或归一化为100%。</Typography.Paragraph>}
    {item.dimension === "age" && <Typography.Paragraph type="secondary">保留来源原始年龄标签，不合并不同年龄分箱。</Typography.Paragraph>}
    {item.dimension !== "province" && <BarChart title={`${audienceDimensions[item.dimension]}占比`} valueLabel="占比" formatValue={(v) => `${v}%`}
      data={item.entries.map((e) => ({ key: e.label, label: e.label, value: e.percentage == null ? null : Number(e.percentage) }))} emptyText="暂无占比，仅有来源指标" />}
    <Table size="small" rowKey="label" pagination={false} dataSource={item.dimension === "province" ? ranked : item.entries}
      columns={[{ title: "来源分类", dataIndex: "label" }, { title: "占比", dataIndex: "percentage", render: percent }, { title: "TGI", dataIndex: "tgi", render: text }]} />
    {item.tgi_source && <Typography.Paragraph type="secondary">TGI来源及口径：{item.tgi_source}</Typography.Paragraph>}
  </>;
}

export function BloggerAudience({ bloggerId, enabled }: { bloggerId: string; enabled: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const [history, setHistory] = useState(false);
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState(false);
  const [form] = Form.useForm<AudienceInput>();
  const readOnly = useReadOnlySupport();
  const qc = useQueryClient();
  const query = useQuery({ queryKey: ["blogger-audience", bloggerId, history, page], queryFn: () => listAudience(bloggerId, history, page), enabled: enabled && expanded });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["blogger-audience", bloggerId] });
    void qc.invalidateQueries({ queryKey: ["bloggers"] });
  };
  const save = useMutation({ mutationFn: (values: AudienceInput) => saveAudience(bloggerId, values),
    onSuccess: (result) => { message.success(result.message); setEditing(false); form.resetFields(); refresh(); },
    onError: (error) => message.error(extractErrorMessage(error, "画像保存失败")),
  });
  const openSource = async (item: AudienceItem) => {
    try { const result = await getAudienceSource(bloggerId, item.id); window.open(result.url, "_blank", "noopener,noreferrer"); }
    catch (error) { message.error(extractErrorMessage(error, "原文件暂时无法打开")); }
  };
  return <>
    <Collapse size="small" style={{ marginTop: 8 }} onChange={(keys) => setExpanded(keys.length > 0)} items={[{ key: "audience", label: "受众画像与采集记录", children: <>
      <Typography.Paragraph type="secondary">各维度独立更新。缺值显示“—”，明确的0保留；完整分布合计允许99%～101%。</Typography.Paragraph>
      <Space wrap style={{ marginBottom: 8 }}>
        <Button size="small" onClick={() => { setHistory(!history); setPage(1); }}>{history ? "查看当前画像" : "查看采集历史"}</Button>
        <Button size="small" onClick={refresh}>刷新画像</Button>
        {!readOnly && query.data?.can_write && <>
          <Button size="small" onClick={() => setEditing(true)}>补录画像</Button>
          <ImportUploadButton source="manual_blogger_audience" label="导入画像" size="small" invalidateKeys={[["blogger-audience"], ["bloggers"]]} successHint="请在导入记录查看整组处理结果，再刷新画像。" />
        </>}
      </Space>
      {(readOnly || query.data && !query.data.can_write) && <Typography.Paragraph type="secondary">当前为只读，可查看画像和采集记录。</Typography.Paragraph>}
      {query.isError ? <Alert type="error" message={extractErrorMessage(query.error, "画像加载失败")} /> : <Table<AudienceItem>
        size="small" rowKey="id" loading={query.isLoading} dataSource={query.data?.items ?? []} locale={{ emptyText: "暂无结构化画像，可按维度局部录入" }}
        pagination={history ? { current: page, total: query.data?.total, pageSize: 10, showSizeChanger: false, onChange: setPage } : false}
        columns={[
          { title: "维度", dataIndex: "dimension", render: (dimension: AudienceInput["dimension"]) => audienceDimensions[dimension] },
          { title: "采集日期", dataIndex: "collected_on" },
          { title: "来源", dataIndex: "source" },
          { title: "范围", render: (_, item) => <>{item.completeness === "partial" ? "部分" : "完整"}{item.estimated ? " · 估计" : ""}{item.is_current ? " · 当前" : ""}</> },
          { title: "记录", render: (_, item) => <Space direction="vertical" size={0}>
            <span>{text(item.created_by_name)}</span>
            {item.import_batch_id ? <>
              <Typography.Text copyable={{ text: item.import_batch_id }}>批次 {item.import_batch_id.slice(0, 8)}</Typography.Text>
              <span>数据行 {item.row_numbers.join("、")}</span>
              {!readOnly && query.data?.can_read_source && <Button type="link" size="small" onClick={() => void openSource(item)}>原文件</Button>}
            </> : <span>手工补录</span>}
          </Space> },
        ]} expandable={{ expandedRowRender: (item) => <AudienceDistribution item={item} /> }} />}
    </> }]} />
    <Modal title="补录单个维度画像" open={editing} width={720} onCancel={() => setEditing(false)} onOk={() => form.submit()} confirmLoading={save.isPending}>
      <Typography.Paragraph type="secondary">23.5或23.5%均表示23.5%；0.235表示0.235%。Top5/缺分类选部分。旧日期仅归档，同维度同日期不同内容会报错。</Typography.Paragraph>
      <Form form={form} layout="vertical" initialValues={{ dimension: "age", completeness: "partial", estimated: false, entries: [{}] }} onFinish={(values) => save.mutate(values)}>
        <Space align="start" wrap>
          <Form.Item name="dimension" label="画像维度" rules={[{ required: true }]}><Select style={{ width: 140 }} options={Object.entries(audienceDimensions).map(([value, label]) => ({ value, label }))} /></Form.Item>
          <Form.Item name="collected_on" label="采集日期" rules={[{ required: true }]}><Input type="date" /></Form.Item>
          <Form.Item name="completeness" label="数据范围" rules={[{ required: true }]}><Select style={{ width: 140 }} options={[{ value: "partial", label: "部分数据" }, { value: "complete", label: "完整分布" }]} /></Form.Item>
          <Form.Item name="estimated" label="估计值" valuePropName="checked"><Switch /></Form.Item>
        </Space>
        <Form.Item name="source" label="采集来源" rules={[{ required: true }]}><Input maxLength={128} /></Form.Item>
        <Typography.Paragraph type="secondary">年龄按原标签填写；城市级别使用一线/新一线/二线/三线/四线/五线/六线及以下/特区/其他。占比或TGI至少填一项；TGI须有实际来源。</Typography.Paragraph>
        <Form.List name="entries">{(fields, { add, remove }) => <>
          {fields.map((field) => <Space key={field.key} align="baseline" wrap>
            <Form.Item name={[field.name, "label"]} label="原始分类" rules={[{ required: true }]}><Input maxLength={64} /></Form.Item>
            <Form.Item name={[field.name, "percentage"]} label="占比（%）"><Input style={{ width: 110 }} /></Form.Item>
            <Form.Item name={[field.name, "tgi"]} label="TGI"><Input style={{ width: 110 }} /></Form.Item>
            <Button disabled={fields.length === 1} onClick={() => remove(field.name)}>移除</Button>
          </Space>)}
          <Button onClick={() => add({})}>添加分类</Button>
        </>}</Form.List>
        <Form.Item name="tgi_source" label="TGI来源及基准口径"><Input maxLength={256} /></Form.Item>
      </Form>
    </Modal>
  </>;
}
