import { useState } from "react";
import {
  Button,
  Card,
  DatePicker,
  Dropdown,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import { DownOutlined, UploadOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import {
  addExtraItem,
  completeAttachmentUpload,
  fillPaymentAmount,
  initAttachmentUpload,
  listSettlements,
  putFileToR2,
  reviewSettlement,
  uploadPaymentProof,
} from "@/features/finance/api";
import type {
  ExtraItemType,
  Settlement,
  SettlementListFilters,
  SettlementStatus,
} from "@/features/finance/types";
import { extractErrorMessage } from "@/services/apiClient";
import { ImportUploadButton } from "@/components/ImportUploadButton";

const money = (v: string | null) => (v == null ? "—" : `¥${v}`);
const STATUS: SettlementStatus[] = [
  "待核查",
  "待付款",
  "待财务付款",
  "已付款",
  "已驳回",
];
const statusColor: Record<string, string> = {
  待核查: "orange",
  待付款: "blue",
  待财务付款: "cyan",
  已付款: "green",
  已驳回: "red",
};
const EXTRA_ITEM_TYPES: ExtraItemType[] = ["运费", "赞奖", "其他"];

/**
 * 财务结款。列对齐 final.xlsx「站外结款表」；操作列按结算状态提供状态推进动作：
 * 待核查 → 核查通过/驳回；待付款 → 填付款金额/增加结算项；待财务付款 → 上传付款凭证。
 */
export function SettlementListPage() {
  const qc = useQueryClient();
  const [filters, setFilters] = useState<SettlementListFilters>({
    page: 1,
    page_size: 20,
  });

  // 弹窗状态
  const [target, setTarget] = useState<Settlement | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [amountOpen, setAmountOpen] = useState(false);
  const [extraOpen, setExtraOpen] = useState(false);
  const [proofOpen, setProofOpen] = useState(false);
  const [rejectForm] = Form.useForm();
  const [amountForm] = Form.useForm();
  const [extraForm] = Form.useForm();
  const [proofForm] = Form.useForm();
  const [proofFile, setProofFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["settlements", filters],
    queryFn: () => listSettlements(filters),
  });

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["settlements"] });
  };

  const approveMutation = useMutation({
    mutationFn: (id: string) => reviewSettlement(id, { action: "approve" }),
    onSuccess: () => {
      message.success("已核查通过");
      refresh();
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      reviewSettlement(id, { action: "reject", review_reason: reason }),
    onSuccess: () => {
      message.success("已驳回");
      setRejectOpen(false);
      refresh();
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const amountMutation = useMutation({
    mutationFn: ({ id, amount }: { id: string; amount: string }) =>
      fillPaymentAmount(id, { payment_amount: amount }),
    onSuccess: () => {
      message.success("付款金额已填写，转「待财务付款」");
      setAmountOpen(false);
      refresh();
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const extraMutation = useMutation({
    mutationFn: ({
      id,
      item_type,
      amount,
      remark,
    }: {
      id: string;
      item_type: ExtraItemType;
      amount: string;
      remark?: string;
    }) => addExtraItem(id, { item_type, amount, remark: remark || null }),
    onSuccess: () => {
      message.success("结算项已添加");
      setExtraOpen(false);
      refresh();
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  async function submitProof(values: { payment_date: dayjs.Dayjs }) {
    if (!target || !proofFile) {
      message.error("请先选择付款截图");
      return;
    }
    setUploading(true);
    try {
      const init = await initAttachmentUpload({
        bucket: "private",
        purpose: "settlement_proof",
        filename: proofFile.name,
        mime_type: proofFile.type || "image/jpeg",
        size_bytes: proofFile.size,
      });
      await putFileToR2(init.presigned_url, proofFile);
      await completeAttachmentUpload(init.attachment_id);
      await uploadPaymentProof(target.id, {
        payment_date: dayjs(values.payment_date).format("YYYY-MM-DD"),
        payment_proof_attachment_id: init.attachment_id,
      });
      message.success("付款凭证已上传，转「已付款」");
      setProofOpen(false);
      setProofFile(null);
      refresh();
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setUploading(false);
    }
  }

  function openReject(r: Settlement) {
    setTarget(r);
    rejectForm.resetFields();
    setRejectOpen(true);
  }
  function openAmount(r: Settlement) {
    setTarget(r);
    amountForm.resetFields();
    setAmountOpen(true);
  }
  function openExtra(r: Settlement) {
    setTarget(r);
    extraForm.resetFields();
    setExtraOpen(true);
  }
  function openProof(r: Settlement) {
    setTarget(r);
    proofForm.resetFields();
    proofForm.setFieldsValue({ payment_date: dayjs() });
    setProofFile(null);
    setProofOpen(true);
  }

  const columns: ColumnsType<Settlement> = [
    {
      title: "月份",
      width: 90,
      render: (_, r) => (r.payment_date || r.created_at || "").slice(0, 7) || "—",
    },
    {
      title: "日期",
      width: 110,
      render: (_, r) => (r.payment_date || r.created_at || "").slice(0, 10) || "—",
    },
    { title: "大类", width: 90, render: () => "站外推广" },
    { title: "项目", width: 80, render: () => "佣金" },
    { title: "货号", dataIndex: "style_code", width: 120, render: (v) => v || "—" },
    { title: "款式", dataIndex: "style_name", width: 140, render: (v) => v || "—" },
    { title: "博主名", dataIndex: "blogger_nickname", width: 120, render: (v) => v || "—" },
    { title: "结算单号", dataIndex: "settlement_no", width: 150 },
    { title: "金额", dataIndex: "amount", width: 100, render: money },
    { title: "付款金额", dataIndex: "payment_amount", width: 110, render: money },
    {
      title: "付款日期",
      dataIndex: "payment_date",
      width: 110,
      render: (v) => v || "—",
    },
    { title: "总成本", dataIndex: "total_amount", width: 100, render: money },
    {
      title: "付款图片",
      width: 90,
      render: (_, r) =>
        r.payment_proof_signed_url ? (
          <a href={r.payment_proof_signed_url} target="_blank" rel="noreferrer">
            查看
          </a>
        ) : (
          "—"
        ),
    },
    {
      title: "结算状态",
      dataIndex: "settlement_status",
      width: 110,
      render: (v: string) => <Tag color={statusColor[v]}>{v}</Tag>,
    },
    { title: "备注", dataIndex: "remark", width: 140, render: (v) => v || "—" },
    {
      title: "操作",
      width: 110,
      fixed: "right",
      render: (_, record) => {
        const s = record.settlement_status;
        const items = [
          {
            key: "approve",
            label: "核查通过",
            disabled: s !== "待核查",
            onClick: () => approveMutation.mutate(record.id),
          },
          {
            key: "reject",
            label: "驳回",
            danger: true,
            disabled: s !== "待核查",
            onClick: () => openReject(record),
          },
          {
            key: "amount",
            label: "填付款金额",
            disabled: s !== "待付款",
            onClick: () => openAmount(record),
          },
          {
            key: "extra",
            label: "增加结算项",
            disabled: s !== "待付款",
            onClick: () => openExtra(record),
          },
          {
            key: "proof",
            label: "上传付款凭证",
            disabled: s !== "待财务付款",
            onClick: () => openProof(record),
          },
        ];
        return (
          <Dropdown menu={{ items }} trigger={["click"]}>
            <Button type="link" size="small">
              操作 <DownOutlined />
            </Button>
          </Dropdown>
        );
      },
    },
  ];

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          财务结款
        </Typography.Title>
      }
      extra={
        <ImportUploadButton
          source="manual_settlement"
          label="导入结款"
          invalidateKeys={[["settlements"]]}
        />
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          placeholder="结算状态"
          allowClear
          style={{ width: 140 }}
          options={STATUS.map((s) => ({ label: s, value: s }))}
          onChange={(v) =>
            setFilters((f) => ({ ...f, settlement_status: v, page: 1 }))
          }
        />
      </Space>
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: 1700 }}
        pagination={{
          current: data?.page ?? 1,
          pageSize: data?.page_size ?? 20,
          total: data?.total ?? 0,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (page, page_size) =>
            setFilters((f) => ({ ...f, page, page_size })),
        }}
      />

      <Modal
        title="驳回结算"
        open={rejectOpen}
        onCancel={() => setRejectOpen(false)}
        onOk={() => rejectForm.submit()}
        confirmLoading={rejectMutation.isPending}
        destroyOnHidden
      >
        <Form
          form={rejectForm}
          layout="vertical"
          onFinish={(v) =>
            target && rejectMutation.mutate({ id: target.id, reason: v.reason })
          }
        >
          <Form.Item
            name="reason"
            label="驳回原因"
            rules={[{ required: true, message: "请输入驳回原因" }]}
          >
            <Input.TextArea rows={3} placeholder="请填写驳回原因" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="填写付款金额"
        open={amountOpen}
        onCancel={() => setAmountOpen(false)}
        onOk={() => amountForm.submit()}
        confirmLoading={amountMutation.isPending}
        destroyOnHidden
      >
        <Form
          form={amountForm}
          layout="vertical"
          onFinish={(v) =>
            target &&
            amountMutation.mutate({
              id: target.id,
              amount: String(v.payment_amount),
            })
          }
        >
          <Form.Item
            name="payment_amount"
            label="付款金额"
            rules={[{ required: true, message: "请输入付款金额" }]}
          >
            <InputNumber
              min={0}
              precision={2}
              style={{ width: "100%" }}
              prefix="¥"
              placeholder="实际付款金额"
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="增加结算项"
        open={extraOpen}
        onCancel={() => setExtraOpen(false)}
        onOk={() => extraForm.submit()}
        confirmLoading={extraMutation.isPending}
        destroyOnHidden
      >
        <Form
          form={extraForm}
          layout="vertical"
          initialValues={{ item_type: "运费" }}
          onFinish={(v) =>
            target &&
            extraMutation.mutate({
              id: target.id,
              item_type: v.item_type,
              amount: String(v.amount),
              remark: v.remark,
            })
          }
        >
          <Form.Item name="item_type" label="类型" rules={[{ required: true }]}>
            <Select
              options={EXTRA_ITEM_TYPES.map((t) => ({ label: t, value: t }))}
            />
          </Form.Item>
          <Form.Item
            name="amount"
            label="金额"
            rules={[{ required: true, message: "请输入金额" }]}
          >
            <InputNumber
              min={0}
              precision={2}
              style={{ width: "100%" }}
              prefix="¥"
            />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input placeholder="备注（可选）" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="上传付款凭证"
        open={proofOpen}
        onCancel={() => setProofOpen(false)}
        onOk={() => proofForm.submit()}
        confirmLoading={uploading}
        destroyOnHidden
      >
        <Form form={proofForm} layout="vertical" onFinish={submitProof}>
          <Form.Item
            name="payment_date"
            label="付款日期"
            rules={[{ required: true, message: "请选择付款日期" }]}
          >
            <DatePicker style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item label="付款截图" required>
            <Upload
              beforeUpload={(file) => {
                setProofFile(file as File);
                return false;
              }}
              maxCount={1}
              accept="image/*"
              onRemove={() => setProofFile(null)}
            >
              <Button icon={<UploadOutlined />}>选择图片</Button>
            </Upload>
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
