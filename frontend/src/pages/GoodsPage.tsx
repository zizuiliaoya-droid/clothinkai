import { useReadOnlySupport } from "@/stores/supportStore";
import { useState } from "react";
import { Button, Card, Segmented, Space, Tabs, Typography } from "antd";
import { useSearchParams } from "react-router-dom";
import { DictManagerModal } from "@/components/DictManager/DictManagerModal";
import { GoodsPanel } from "@/pages/goods/GoodsPanel";
import { StylePanel } from "@/pages/goods/StylePanel";
import { CostTablePage } from "@/pages/CostTablePage";
import { ProductImportToolbar } from "@/pages/goods/ProductImportToolbar";

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
  const readOnly = useReadOnlySupport();
  const [searchParams, setSearchParams] = useSearchParams();
  const [dictOpen, setDictOpen] = useState(false);
  const activeTab = toTabKey(searchParams.get("tab"));
  const costView = searchParams.get("view") === "costs" && !searchParams.get("style_id");
  const openStyleId = readOnly ? undefined : searchParams.get("style_id") ?? undefined;

  function switchTab(key: string) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (key === "styles") next.set("tab", "styles");
        else next.delete("tab");
        next.delete("style_id");
        next.delete("view");
        return next;
      },
      { replace: true }
    );
  }

  function switchStyleView(view: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("tab", "styles");
      if (view === "costs") next.set("view", "costs");
      else next.delete("view");
      next.delete("style_id");
      return next;
    }, { replace: true });
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
        <Space wrap style={{ display: "flex", justifyContent: "space-between", whiteSpace: "normal", padding: "8px 0" }}>
          <Typography.Title level={4} style={{ margin: 0 }}>商品与款式</Typography.Title>
          {!readOnly && <Button onClick={() => setDictOpen(true)}>管理字典</Button>}
        </Space>
      }
    >
      <Typography.Paragraph type="secondary">商品用于组合单品或套装；款式用于维护货品、颜色尺码与成本。</Typography.Paragraph>
      <ProductImportToolbar />
      <Tabs
        activeKey={activeTab}
        onChange={switchTab}
        items={[
          { key: "goods", label: "商品 / 套装", children: activeTab === "goods" ? <GoodsPanel /> : null },
          {
            key: "styles",
            label: "款式",
            children: (
              activeTab === "styles" ? <>
                <Segmented
                  aria-label="款式视图"
                  value={costView ? "costs" : "details"}
                  options={[{ label: "款式资料", value: "details" }, { label: "规格与成本", value: "costs" }]}
                  onChange={switchStyleView}
                  style={{ marginBottom: 16 }}
                />
                {costView ? <CostTablePage /> : <StylePanel openStyleId={openStyleId} onOpenStyleClosed={clearStyleId} />}
              </> : null
            ),
          },
        ]}
      />
      {!readOnly && <DictManagerModal open={dictOpen} onClose={() => setDictOpen(false)} />}
    </Card>
  );
}
