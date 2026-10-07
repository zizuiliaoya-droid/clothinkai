import { useEffect, useState } from "react";
import { Button, Input, Select, Space, Table, Tag, Tooltip, Typography, message } from "antd";
import { PlusOutlined, SearchOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import {
  disableStyle,
  enableStyle,
  goodsDisplayName,
  listStyles,
} from "@/features/product/api";
import type { Style, StyleListFilters } from "@/features/product/types";
import { extractErrorMessage } from "@/services/apiClient";
import { StyleImageThumbnail } from "@/components/StyleImageThumbnail/StyleImageThumbnail";
import { StyleEditModal } from "./StyleEditModal";

type StatusFilter = "active" | "inactive" | "all";

/** 状态下拉 → 后端三态参数。后端：都不传=仅启用，is_active=false=仅停用，include_inactive=true=全部。 */
function statusParams(status: StatusFilter): Pick<
  StyleListFilters,
  "is_active" | "include_inactive"
> {
  if (status === "inactive") return { is_active: false, include_inactive: true };
  if (status === "all") return { is_active: undefined, include_inactive: true };
  return { is_active: undefined, include_inactive: undefined };
}

/** 关闭时只翻 open，保留 mode / styleId，弹窗关闭动画里标题不跳。 */
interface ModalState {
  open: boolean;
  mode: "create" | "edit";
  styleId?: string;
}

export interface StylePanelProps {
  /** 来自 URL ?style_id=：直接打开该款式的编辑弹窗。 */
  openStyleId?: string;
  /** 由 openStyleId 打开的弹窗关闭后回调（外壳据此把 ?style_id= 从地址栏去掉）。 */
  onOpenStyleClosed?: () => void;
}

/**
 * 款式维护（商品 / 套装页的「款式」页签，8a-1）。
 *
 * 搜全部款式（含不属于任何商品的、已停用的），能看出归属商品（显示商品名，不显示编码）；
 * 新建、编辑、停用 / 启用。不提供删除（原页面也没有）。简称、季节、品牌在商品层维护，类目已下线。
 */
export function StylePanel({ openStyleId, onOpenStyleClosed }: StylePanelProps) {
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("active");
  const [filters, setFilters] = useState<StyleListFilters>({
    page: 1,
    page_size: 10,
  });
  const [modal, setModal] = useState<ModalState>({ open: false, mode: "create" });
  const [fromUrl, setFromUrl] = useState(false);

  useEffect(() => {
    if (!openStyleId) return;
    setModal({ open: true, mode: "edit", styleId: openStyleId });
    setFromUrl(true);
  }, [openStyleId]);

  const { data, isLoading } = useQuery({
    queryKey: ["styles", filters],
    queryFn: () => listStyles(filters),
  });

  // 停用 ↔ 启用 走 disable/enable（操作 is_active）。
  // 不要用 restoreStyle —— 那个是恢复软删（is_deleted），对未软删的款式会直接报错。
  const toggleMutation = useMutation({
    mutationFn: (record: Style) =>
      record.is_active ? disableStyle(record.id) : enableStyle(record.id),
    onSuccess: (_data, record) => {
      message.success(record.is_active ? "款式已停用" : "款式已启用");
      void qc.invalidateQueries({ queryKey: ["styles"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function closeModal() {
    setModal((m) => ({ ...m, open: false }));
    if (fromUrl) {
      setFromUrl(false);
      onOpenStyleClosed?.();
    }
  }

  const columns: ColumnsType<Style> = [
    {
      title: "主图",
      dataIndex: "main_image_url",
      width: 72,
      fixed: "left",
      render: (src: string | null, record) => (
        <StyleImageThumbnail src={src} alt={`${record.style_code} 款式主图`} />
      ),
    },
    { title: "货号", dataIndex: "style_code", width: 140, fixed: "left" },
    { title: "款名", dataIndex: "style_name" },
    {
      title: "所属商品",
      dataIndex: "goods_title",
      width: 240,
      render: (_: string | null, record) => {
        const name = goodsDisplayName(record.goods_title, record.goods_short_name);
        if (!record.goods_code && !name) {
          return <Typography.Text type="secondary">未归属</Typography.Text>;
        }
        return (
          <Space size={4} direction="vertical">
            <Space size={4}>
              <Tooltip title={record.goods_title || undefined}>
                <span>{name || "—"}</span>
              </Tooltip>
              {record.goods_is_suit && <Tag color="purple">套装</Tag>}
            </Space>
            {/* 款式可以既单卖又进套装，那时主商品是单品、套装名另外标出来 */}
            {record.suite_name && !record.goods_is_suit && (
              <Tooltip title={record.suite_name}>
                <Tag
                  color="blue"
                  style={{ maxWidth: 224, overflow: "hidden", textOverflow: "ellipsis" }}
                >
                  另在套装：{record.suite_name}
                </Tag>
              </Tooltip>
            )}
          </Space>
        );
      },
    },
    { title: "性别", dataIndex: "gender", width: 70, render: (v: string | null) => v || "—" },
    {
      title: "颜色明细",
      dataIndex: "tag_color",
      width: 180,
      render: (v: string[] | null) =>
        v && v.length > 0 ? (
          <Space size={[0, 4]} wrap>
            {v.map((c) => (
              <Tag key={c} style={{ marginInlineEnd: 4 }}>
                {c}
              </Tag>
            ))}
          </Space>
        ) : (
          "—"
        ),
    },
    {
      title: "状态",
      dataIndex: "is_active",
      width: 90,
      render: (v: boolean) =>
        v ? <Tag color="green">启用</Tag> : <Tag color="red">停用</Tag>,
    },
    {
      title: "操作",
      width: 150,
      render: (_, record) => (
        <Space>
          <Button
            type="link"
            size="small"
            onClick={() => setModal({ open: true, mode: "edit", styleId: record.id })}
          >
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            danger={record.is_active}
            loading={toggleMutation.isPending && toggleMutation.variables?.id === record.id}
            onClick={() => toggleMutation.mutate(record)}
          >
            {record.is_active ? "停用" : "启用"}
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <>
      <Space style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }} wrap>
        <Space wrap>
          <Input.Search
            placeholder="搜索货号 / 款名"
            allowClear
            style={{ width: 240 }}
            enterButton={<SearchOutlined />}
            onSearch={(v) =>
              setFilters((f) => ({ ...f, keyword: v || undefined, page: 1 }))
            }
          />
          <Select
            value={statusFilter}
            style={{ width: 120 }}
            aria-label="状态"
            options={[
              { label: "仅启用", value: "active" },
              { label: "仅停用", value: "inactive" },
              { label: "全部", value: "all" },
            ]}
            onChange={(v: StatusFilter) => {
              setStatusFilter(v);
              setFilters((f) => ({ ...f, ...statusParams(v), page: 1 }));
            }}
          />
        </Space>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setModal({ open: true, mode: "create" })}
        >
          新建款式
        </Button>
      </Space>

      <Table
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: 1100 }}
        pagination={{
          current: data?.page ?? 1,
          pageSize: data?.page_size ?? 10,
          total: data?.total ?? 0,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (page, page_size) =>
            setFilters((f) => ({ ...f, page, page_size })),
        }}
      />

      <StyleEditModal
        open={modal.open}
        mode={modal.mode}
        styleId={modal.styleId}
        onClose={closeModal}
      />
    </>
  );
}
