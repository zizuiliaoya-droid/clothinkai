import { Alert, Space, Tag } from "antd";
import { promotionStageStyle } from "@/features/flow/stageStyle";
import { BADGE_LABELS, QUICK_FILTERS, quickCount, quickSelected, toggleQuickFilter } from "../promotionStages";
import type { Promotion, PromotionBadge, PromotionListFilters, PromotionStageCounts } from "../types";

export function PromotionQuickFilters({ filters, counts, error, onChange }: {
  filters: PromotionListFilters; counts?: PromotionStageCounts; error?: boolean;
  onChange: (patch: Partial<PromotionListFilters>) => void;
}) {
  return <div style={{ marginBottom: 12 }} aria-label="阶段快捷筛选">
    {error && <Alert type="warning" message="阶段计数暂不可用，可继续筛选" style={{ marginBottom: 8 }} />}
    <Space wrap size={[4, 8]}>{QUICK_FILTERS.map((item) => {
      const selected = quickSelected(filters, item);
      const change = () => onChange(toggleQuickFilter(filters, item));
      return <button key={item.label} type="button" className="promotion-quick-filter" aria-pressed={selected} onClick={change}>
        <Tag.CheckableTag checked={selected}>
        {item.label}（{quickCount(counts, item) ?? "—"}）
        </Tag.CheckableTag>
      </button>;
    })}</Space>
  </div>;
}

export function PromotionStageCell({ row }: { row: Promotion }) {
  const stage = promotionStageStyle(row.stage);
  return <Space direction="vertical" size={4}>
    <Tag color={stage.color}>{stage.label}</Tag>
    <Space wrap size={[0, 4]}>{row.badges?.map((badge) => {
      const label = BADGE_LABELS[badge as PromotionBadge];
      return label ? <Tag key={badge} color={badge === "metrics_due" ? "blue" : badge === "missing_qr" ? "orange" : undefined}>
        {badge === "freight_open" && row.freight_payment ? `运费 · ${row.freight_payment.status}` : label}
      </Tag> : null;
    })}</Space>
  </Space>;
}
