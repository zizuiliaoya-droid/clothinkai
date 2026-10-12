// 主线抽屉（流程线设计 6.6、8.1）：头信息 + 「下一步」+ 时间线 + 可选页签 + 留言框。推广列表、谈款页、结款页共用。
// - 打开时拉一次时间线；抽屉里做了任何动作后由调用方 invalidate lineEventsKey(lineId) 重新拉，不做推送
// - 头信息不显示商品编码：商品简称、博主由页面行传入（读接口只回单据号与阶段）
// - 留言框只在 ui.actions.comment 可点时出现
// - 宽度 min(720px, 100vw)；Esc 关闭，关闭后焦点回到打开它的元素（8.9）
import { useEffect, useRef, type ReactNode } from "react";
import { Drawer, Space, Spin, Tabs, Tag, Typography, theme, type TabsProps } from "antd";
import { useQuery } from "@tanstack/react-query";
import { getLineEvents, lineEventsKey } from "./api";
import { LineCommentBox } from "./LineCommentBox";
import { LineTimeline } from "./LineTimeline";
import { negotiationStatusStyle, promotionStageStyle } from "./stageStyle";

export interface LineDrawerProps {
  /** null = 抽屉关着。 */
  lineId: string | null;
  onClose: () => void;
  /** 商品简称（没填回落全称），由页面行传入。 */
  goodsName?: string | null;
  bloggerName?: string | null;
  /** 时间线下方的页签（推广列表：单据详情 / 金额改动 / 催发记录）；不传就不出。 */
  tabs?: TabsProps["items"];
  /** 头信息右侧的附加内容（如动作按钮）。 */
  extra?: ReactNode;
}

export const LINE_NOT_FOUND_TEXT = "看不到这条记录（没有权限，或单据已删除）";

export function LineDrawer({ lineId, onClose, goodsName, bloggerName, tabs, extra }: LineDrawerProps) {
  const { token } = theme.useToken();
  const opener = useRef<HTMLElement | null>(null);
  const open = lineId !== null;

  useEffect(() => {
    if (open && document.activeElement instanceof HTMLElement) opener.current = document.activeElement;
  }, [open]);

  const { data, isLoading, error } = useQuery({
    queryKey: lineEventsKey(lineId),
    queryFn: () => getLineEvents(lineId as string),
    enabled: open,
  });

  const promo = data?.promotion ?? null;
  const neg = data?.negotiation ?? null;
  const stage = promo ? promotionStageStyle(promo.stage) : neg ? negotiationStatusStyle(neg.status) : null;
  const secondary = { color: token.colorTextSecondary };

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width="min(720px, 100vw)"
      destroyOnHidden
      afterOpenChange={(visible) => {
        if (!visible) {
          opener.current?.focus();
          opener.current = null;
        }
      }}
      extra={extra}
      title={
        <Space size={8} wrap>
          <span>{promo ? promo.internal_code : "谈款"}</span>
          {stage && <Tag color={stage.color}>{stage.label}</Tag>}
          {goodsName && <span style={secondary}>{goodsName}</span>}
          {bloggerName && <span style={secondary}>· {bloggerName}</span>}
        </Space>
      }
    >
      {isLoading ? (
        <Spin />
      ) : error || !data || !lineId ? (
        <Typography.Text>{LINE_NOT_FOUND_TEXT}</Typography.Text>
      ) : (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <LineTimeline data={data} />
          {tabs && tabs.length > 0 && <Tabs items={tabs} />}
          {data.ui?.actions?.comment?.state === "enabled" && <LineCommentBox lineId={lineId} />}
        </Space>
      )}
    </Drawer>
  );
}
