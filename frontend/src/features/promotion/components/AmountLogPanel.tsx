import { Space, Tag, Timeline, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import { promotionAmountLog } from "@/features/promotion/api";

const AMOUNT_FIELD_LABEL: Record<string, string> = {
  quote_amount: "博主服务费",
  cost_snapshot: "样品成本",
  return_shipping_fee: "寄回运费",
};

/** 「模式兜底」标红：那是系统按合作模式改写的，不是人填错了。 */
const amountSourceColor: Record<string, string> = {
  手动编辑: "blue",
  模式初始化: "default",
  模式兜底: "orange",
};

function fmtAmount(v: string | null): string {
  return v == null ? "—" : `¥${Number(v).toFixed(2)}`;
}

/**
 * 金额改动记录（PRD 第 10 节第 14 条：成本修改可追溯）。
 *
 * 后端按**字段级**权限门控，看不到金额的角色（运营等）会拿到 403 ——
 * 这里把 403 显示成「无权查看」而不是报错弹窗。
 */
export function AmountLogPanel({ promotionId }: { promotionId: string | null }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["promotion-amount-log", promotionId],
    queryFn: () => promotionAmountLog(promotionId as string),
    enabled: !!promotionId,
  });

  if (isLoading) return <Typography.Text type="secondary">加载中…</Typography.Text>;
  if (error) {
    return (
      <Typography.Text type="secondary">
        无权查看金额改动记录（需要有报价字段的读权限）
      </Typography.Text>
    );
  }
  if ((data?.length ?? 0) === 0) {
    return <Typography.Text type="secondary">这单金额没有改动过</Typography.Text>;
  }
  return (
    <Timeline
      items={(data ?? []).map((r) => ({
        color: r.change_source === "模式兜底" ? "orange" : "blue",
        children: (
          <Space direction="vertical" size={2}>
            <Space size={6}>
              <Typography.Text strong>
                {AMOUNT_FIELD_LABEL[r.field_name] ?? r.field_name}
              </Typography.Text>
              <Tag color={amountSourceColor[r.change_source]}>
                {r.change_source}
              </Tag>
            </Space>
            <Typography.Text>
              {fmtAmount(r.before_value)} → {fmtAmount(r.after_value)}
            </Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {dayjs(r.created_at).format("YYYY-MM-DD HH:mm")}
              {r.changed_by_name ? ` · ${r.changed_by_name}` : ""}
            </Typography.Text>
          </Space>
        ),
      }))}
    />
  );
}
