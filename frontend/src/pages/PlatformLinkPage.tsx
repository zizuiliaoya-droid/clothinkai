import { useState } from "react";
import {
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import {
  deletePlatformLink,
  listGoodsForStyle,
  listPlatformLinks,
  updatePlatformLink,
  type GoodsOption,
  type PlatformLink,
  type PlatformLinkFilters,
} from "@/features/product/api";
import { extractErrorMessage } from "@/services/apiClient";

const PLATFORMS = ["千牛", "万相台"];
const CHANNELS = ["普通", "直播"];

/**
 * 平台链接运维视图。
 *
 * 「平台链接」= 店铺里一条实际在卖的链接（千牛商品ID / 万相台主体ID）。它决定三件事：
 * 销售数据算到哪个商品头上、仓库发的是哪件衣服、以及这笔 GMV 算普通还是直播。
 *
 * 业务页面（款式管理）刻意不展示平台ID —— 绑错一条链接，整条销售数据就记到别的商品名下，
 * 这是运维职责。路由与菜单都限管理员 / 运营。
 */
export function PlatformLinkPage() {
  const qc = useQueryClient();
  const [filters, setFilters] = useState<PlatformLinkFilters>({
    page: 1,
    page_size: 20,
  });
  const [editing, setEditing] = useState<PlatformLink | null>(null);
  const [form] = Form.useForm();

  const { data, isLoading } = useQuery({
    queryKey: ["platform-links", filters],
    queryFn: () => listPlatformLinks(filters),
  });
  // 改归属只能在「该链接所绑款式所属的商品」里选，否则销售数据会记到不含这件衣服的商品上
  const { data: goodsOptions } = useQuery({
    queryKey: ["goods", "by-style", editing?.style_id],
    enabled: !!editing,
    queryFn: () => listGoodsForStyle(editing!.style_id),
  });

  const saveMutation = useMutation({
    mutationFn: (values: {
      goods_main_id?: string;
      channel?: string;
      title?: string | null;
      is_active?: boolean;
    }) => updatePlatformLink(editing!.id, values),
    onSuccess: () => {
      message.success("已保存，报表归属会跟着调整");
      setEditing(null);
      form.resetFields();
      void qc.invalidateQueries({ queryKey: ["platform-links"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deletePlatformLink(id),
    onSuccess: () => {
      message.success("链接已删除");
      void qc.invalidateQueries({ queryKey: ["platform-links"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function openEdit(record: PlatformLink) {
    setEditing(record);
    form.resetFields();
    form.setFieldsValue({
      goods_main_id: record.goods_main_id ?? undefined,
      channel: record.channel,
      title: record.title,
      is_active: record.is_active,
    });
  }

  const columns: ColumnsType<PlatformLink> = [
    { title: "平台", dataIndex: "platform", width: 90, fixed: "left" },
    {
      title: "平台ID",
      dataIndex: "platform_id",
      width: 150,
      fixed: "left",
      render: (v: string) => <Typography.Text copyable>{v}</Typography.Text>,
    },
    {
      title: "渠道",
      dataIndex: "channel",
      width: 90,
      render: (v: string) =>
        v === "直播" ? <Tag color="magenta">直播</Tag> : <Tag>普通</Tag>,
    },
    {
      title: "归属商品",
      dataIndex: "goods_code",
      width: 180,
      render: (code: string | null, row) =>
        code ? (
          <Space size={4}>
            <Tooltip title={row.goods_title ?? undefined}>
              <span>{code}</span>
            </Tooltip>
            {row.goods_is_suit && <Tag color="purple">套装</Tag>}
          </Space>
        ) : (
          <Tag color="red">未归属</Tag>
        ),
    },
    {
      title: "关联款式",
      dataIndex: "style_code",
      width: 200,
      render: (code: string | null, row) =>
        code ? `${code} ${row.style_name ?? ""}`.trim() : "—",
    },
    {
      title: "平台标题",
      dataIndex: "title",
      ellipsis: true,
      render: (v: string | null) => v || "—",
    },
    {
      title: "状态",
      dataIndex: "is_active",
      width: 80,
      render: (v: boolean) =>
        v ? <Tag color="green">启用</Tag> : <Tag color="red">停用</Tag>,
    },
    {
      title: "操作",
      width: 120,
      fixed: "right",
      render: (_, record) => (
        <Space>
          <Button type="link" size="small" onClick={() => openEdit(record)}>
            编辑
          </Button>
          <Popconfirm
            title="删除这条链接？"
            description="删除后该链接的销售/广告数据将不再计入任何商品。"
            onConfirm={() => deleteMutation.mutate(record.id)}
          >
            <Button type="link" size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          平台链接
        </Typography.Title>
      }
    >
      <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
        店铺里每条在卖的链接与商品、款式、渠道的绑定关系。绑错会让销售数据算到别的商品头上，
        所以这个视图只对管理员与运营开放。
      </Typography.Paragraph>

      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          placeholder="平台ID / 货号 / 款名 / 商品编码"
          allowClear
          style={{ width: 260 }}
          onSearch={(v) =>
            setFilters((f) => ({ ...f, keyword: v || undefined, page: 1 }))
          }
        />
        <Select
          placeholder="平台"
          allowClear
          style={{ width: 120 }}
          options={PLATFORMS.map((p) => ({ label: p, value: p }))}
          onChange={(v) => setFilters((f) => ({ ...f, platform: v, page: 1 }))}
        />
        <Select
          placeholder="渠道"
          allowClear
          style={{ width: 110 }}
          options={CHANNELS.map((c) => ({ label: c, value: c }))}
          onChange={(v) => setFilters((f) => ({ ...f, channel: v, page: 1 }))}
        />
        <Space size={4}>
          <Switch
            checked={filters.unmapped_only ?? false}
            onChange={(v) =>
              setFilters((f) => ({ ...f, unmapped_only: v || undefined, page: 1 }))
            }
          />
          <Tooltip title="这些链接的销售与广告数据进不了任何商品的报表">
            <span>只看未归属商品</span>
          </Tooltip>
        </Space>
      </Space>

      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: 1200 }}
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
        title={
          editing
            ? `编辑链接 · ${editing.platform} ${editing.platform_id}`
            : "编辑链接"
        }
        open={!!editing}
        onCancel={() => setEditing(null)}
        onOk={() => form.submit()}
        confirmLoading={saveMutation.isPending}
        destroyOnHidden
        width={520}
      >
        <Form
          form={form}
          layout="vertical"
          style={{ marginTop: 16 }}
          onFinish={(v) => saveMutation.mutate(v)}
        >
          <Form.Item
            name="goods_main_id"
            label="归属商品"
            tooltip="决定这条链接的销售额算给哪个商品。只能选包含该款式的商品。"
          >
            <Select
              placeholder="选择归属商品"
              options={(goodsOptions ?? []).map((g: GoodsOption) => ({
                label: `${g.goods_code} ${g.goods_title}${g.is_suit ? "（套装）" : ""}`,
                value: g.goods_main_id,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="channel"
            label="渠道"
            tooltip="直播链接的 GMV 单独核算，不计入店铺总销售额。"
          >
            <Select options={CHANNELS.map((c) => ({ label: c, value: c }))} />
          </Form.Item>
          <Form.Item name="title" label="平台标题">
            <Input placeholder="平台侧商品标题（可选）" allowClear />
          </Form.Item>
          <Form.Item name="is_active" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
          {editing && (
            <Typography.Text type="secondary">
              关联款式：{editing.style_code ?? "—"} {editing.style_name ?? ""}
              （款式不在这里改，要改去款式管理）
            </Typography.Text>
          )}
        </Form>
      </Modal>
    </Card>
  );
}
