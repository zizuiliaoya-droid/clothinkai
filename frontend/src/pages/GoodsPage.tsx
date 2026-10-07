import { useState } from "react";
import { Button, Card, Tabs, Typography } from "antd";
import { useSearchParams } from "react-router-dom";
import { DictManagerModal } from "@/components/DictManager/DictManagerModal";
import { GoodsPanel } from "@/pages/goods/GoodsPanel";
import { StylePanel } from "@/pages/goods/StylePanel";

type TabKey = "goods" | "styles";

function toTabKey(v: string | null): TabKey {
  return v === "styles" ? "styles" : "goods";
}

/**
 * 商品 / 套装页（8a-1 起款式维护也并在这里）。
 *
 * 两个页签：「商品 / 套装」是报表归属的主体；「款式」维护商品引用的款式（货品）。
 * 页签与地址栏 ``?tab=`` 同步，``?style_id=`` 直接打开该款式的编辑（旧的 /styles 书签会跳到这里）。
 * 标题栏的「管理字典」维护季节 / 颜色 / 尺码。
 */
export function GoodsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [dictOpen, setDictOpen] = useState(false);
  const activeTab = toTabKey(searchParams.get("tab"));
  const openStyleId = searchParams.get("style_id") ?? undefined;

  function switchTab(key: string) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (key === "styles") next.set("tab", "styles");
        else next.delete("tab");
        next.delete("style_id");
        return next;
      },
      { replace: true }
    );
  }

  function clearStyleId() {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("style_id");
        return next;
      },
      { replace: true }
    );
  }

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          商品 / 套装
        </Typography.Title>
      }
      extra={<Button onClick={() => setDictOpen(true)}>管理字典</Button>}
    >
      <Tabs
        activeKey={activeTab}
        onChange={switchTab}
        items={[
          { key: "goods", label: "商品 / 套装", children: <GoodsPanel /> },
          {
            key: "styles",
            label: "款式",
            children: (
              <StylePanel openStyleId={openStyleId} onOpenStyleClosed={clearStyleId} />
            ),
          },
        ]}
      />
      <DictManagerModal open={dictOpen} onClose={() => setDictOpen(false)} />
    </Card>
  );
}
