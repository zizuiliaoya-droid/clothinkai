import { useState } from "react";
import { Popover, Space, Spin, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { bloggerCooperationHistory } from "@/features/negotiation/api";
import type { BloggerCooperationItem } from "@/features/negotiation/types";

const modeColor: Record<string, string> = {
  寄拍: "blue",
  送拍: "green",
  置换: "purple",
};

const publishColor: Record<string, string> = {
  未发布: "default",
  已发布: "green",
  已取消: "orange",
  异常: "red",
};

/**
 * 博主悬浮卡：展示最近几次合作过的款式（PRD V1.4 改动 3）。
 *
 * PRD 原文还要「当时 ROI」。博主维度的 ROI 系统里从来没定义过 —— 现有 ROI 都是
 * 款式/商品维度，而且要先定「发布后多少天内算这次合作的效果」这个窗口。所以这里先给
 * 已有口径的单赞成本（CPL）与点赞数，同样能看出这个博主推得怎么样。
 *
 * 数据取自推广单而不是谈款单：草稿和被驳回的谈款没真推出去，不算历史合作。
 *
 * 只在真正 hover 时才发请求（``enabled: open``），列表里几十个博主不会一次打几十个接口。
 */
export function BloggerHoverCard({
  bloggerId,
  bloggerName,
  children,
  limit = 5,
}: {
  bloggerId: string;
  bloggerName?: string | null;
  children: ReactNode;
  limit?: number;
}) {
  const [open, setOpen] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["blogger-history", bloggerId, limit],
    queryFn: () => bloggerCooperationHistory(bloggerId, limit),
    enabled: open,
    staleTime: 60_000,
  });

  const columns: ColumnsType<BloggerCooperationItem> = [
    {
      title: "款式",
      dataIndex: "style_code",
      width: 150,
      render: (code: string, row) => (
        <Space size={6}>
          {row.style_main_image_url ? (
            <img
              src={row.style_main_image_url}
              alt={`${code} 款式图`}
              style={{
                width: 32,
                height: 32,
                objectFit: "cover",
                borderRadius: 4,
                border: "1px solid #f0f0f0",
              }}
            />
          ) : (
            <span
              aria-hidden
              style={{
                width: 32,
                height: 32,
                display: "inline-block",
                background: "#fafafa",
                borderRadius: 4,
                border: "1px solid #f0f0f0",
              }}
            />
          )}
          <span>
            <div>{code}</div>
            {row.style_name && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {row.style_name}
              </Typography.Text>
            )}
          </span>
        </Space>
      ),
    },
    {
      title: "合作",
      dataIndex: "cooperation_date",
      width: 150,
      render: (d: string, row) => (
        <Space size={4}>
          <span>{d}</span>
          {row.cooperation_mode && (
            <Tag color={modeColor[row.cooperation_mode]}>{row.cooperation_mode}</Tag>
          )}
        </Space>
      ),
    },
    {
      title: "状态",
      dataIndex: "publish_status",
      width: 80,
      render: (v: string) => <Tag color={publishColor[v]}>{v}</Tag>,
    },
    {
      title: "点赞",
      dataIndex: "like_count",
      width: 70,
      align: "right",
      render: (v: number | null) => (v == null ? "—" : v),
    },
    {
      title: "单赞成本",
      dataIndex: "cpl",
      width: 90,
      align: "right",
      render: (v: string | null) => (v == null ? "—" : `¥${Number(v).toFixed(2)}`),
    },
  ];

  const content = (
    <div style={{ width: 560 }}>
      <Typography.Text strong>历史合作款式</Typography.Text>
      {data && (
        <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
          共 {data.total_cooperations} 次，显示最近 {data.items.length} 次
        </Typography.Text>
      )}
      <div style={{ marginTop: 8 }}>
        {isLoading ? (
          <Spin style={{ padding: 16 }} />
        ) : (data?.items.length ?? 0) === 0 ? (
          <Typography.Text type="secondary">还没有合作记录</Typography.Text>
        ) : (
          <Table
            rowKey="promotion_id"
            size="small"
            pagination={false}
            columns={columns}
            dataSource={data?.items ?? []}
          />
        )}
      </div>
    </div>
  );

  return (
    <Popover
      content={content}
      title={bloggerName ?? undefined}
      trigger="hover"
      placement="rightTop"
      open={open}
      onOpenChange={setOpen}
      mouseEnterDelay={0.3}
    >
      <span style={{ cursor: "pointer", borderBottom: "1px dashed #d9d9d9" }}>
        {children}
      </span>
    </Popover>
  );
}
