// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "@/stores/authStore";
import { PromotionColumnSettings } from "./PromotionColumnSettings";
import { usePromotionColumnPreference } from "./usePromotionColumnPreference";
import { getPromotionColumnPreference, savePromotionColumnPreference, visibleColumnKeys, type ColumnOption, type ColumnPreference } from "./columnPreference";

vi.mock("./columnPreference", async (original) => ({
  ...await original<typeof import("./columnPreference")>(),
  getPromotionColumnPreference: vi.fn(), savePromotionColumnPreference: vi.fn(),
}));
const options: ColumnOption[] = [
  { key: "code", label: "内部编码" }, { key: "stage", label: "当前阶段" },
  { key: "content_category", label: "内容分类" }, { key: "quote", label: "报价" },
];
const user = { id: "user-a", username: "a", display_name: null, email: null, status: "active", roles: ["admin"] };
let clients: QueryClient[] = [];
beforeEach(() => {
  const originalStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) => originalStyle(element));
  useAuthStore.setState({ user, isAuthenticated: true });
  vi.mocked(getPromotionColumnPreference).mockReset().mockResolvedValue({ order: [], hidden: [] });
  vi.mocked(savePromotionColumnPreference).mockReset().mockResolvedValue();
});
afterEach(() => { cleanup(); clients.forEach((client) => client.clear()); clients = []; vi.restoreAllMocks(); });

function Harness({ available = options }: { available?: ColumnOption[] }) {
  const settings = usePromotionColumnPreference();
  return <><span>{settings.loading ? "loading" : "ready"}</span>
    <output aria-label="当前列">{visibleColumnKeys(available.map((option) => option.key), settings.preference).join(",")}</output>
    <PromotionColumnSettings key={settings.userId} options={available} {...settings} /></>;
}
function mount(available = options) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  clients.push(client);
  const tree = (next: ColumnOption[]) => <QueryClientProvider client={client}><Harness available={next} /></QueryClientProvider>;
  return { ...render(tree(available)), tree };
}
async function open() { await screen.findByText("ready"); fireEvent.click(screen.getByRole("button", { name: "列设置" })); await screen.findByRole("dialog"); }
async function save() { fireEvent.click(screen.getByRole("button", { name: "保存" })); await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull()); }

describe("推广列偏好", () => {
  it("勾选、拖排、上下移动保存后刷新恢复，恢复默认再保存", async () => {
    let stored: ColumnPreference = { order: [], hidden: [] };
    vi.mocked(getPromotionColumnPreference).mockImplementation(async () => stored);
    vi.mocked(savePromotionColumnPreference).mockImplementation(async (next) => { stored = next; });
    const first = mount(); await open();
    fireEvent.click(screen.getByRole("checkbox", { name: "报价" }));
    const list = screen.getByRole("list", { name: "列顺序" });
    const rows = within(list).getAllByRole("listitem");
    let dragValue = "";
    const dataTransfer = { setData: (_type: string, value: string) => { dragValue = value; }, getData: () => dragValue };
    fireEvent.dragStart(rows[2], { dataTransfer }); fireEvent.drop(rows[0], { dataTransfer });
    fireEvent.click(screen.getByRole("button", { name: "下移内容分类" }));
    await save();
    expect(screen.getByLabelText("当前列").textContent).toBe("code,content_category,stage");
    first.unmount(); mount(); await screen.findByText("ready");
    expect(screen.getByLabelText("当前列").textContent).toBe("code,content_category,stage");
    await open(); fireEvent.click(screen.getByRole("button", { name: "恢复默认" })); await save();
    expect(screen.getByLabelText("当前列").textContent).toBe("code,stage,content_category,quote");
  });

  it("未知列忽略、新列补入；字段权限收紧不会从旧偏好恢复隐藏字段", async () => {
    vi.mocked(getPromotionColumnPreference).mockResolvedValue({ order: ["removed", "quote", "stage"], hidden: [] });
    const view = mount(); await screen.findByText("ready");
    expect(screen.getByLabelText("当前列").textContent).toBe("quote,stage,code,content_category");
    view.rerender(view.tree(options.filter((option) => option.key !== "quote")));
    expect(screen.getByLabelText("当前列").textContent).toBe("stage,code,content_category");
    await open();
    expect(screen.queryByRole("checkbox", { name: "报价" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "恢复默认" })); await save();
    expect(screen.getByLabelText("当前列").textContent).not.toContain("quote");
  });

  it("读取失败能重试；保存失败保留选择且不改变当前表格", async () => {
    vi.mocked(getPromotionColumnPreference).mockRejectedValueOnce(new Error("read failed"));
    mount(); await screen.findByRole("button", { name: "列设置加载失败，重试" });
    fireEvent.click(screen.getByRole("button", { name: "列设置加载失败，重试" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "列设置" }) as HTMLButtonElement).disabled).toBe(false));
    await open(); fireEvent.click(screen.getByRole("checkbox", { name: "报价" }));
    vi.mocked(savePromotionColumnPreference).mockRejectedValueOnce(new Error("save failed"));
    fireEvent.click(screen.getByRole("button", { name: "保存" })); await screen.findByText("save failed");
    expect((screen.getByRole("checkbox", { name: "报价" }) as HTMLInputElement).checked).toBe(false);
    expect(screen.getByLabelText("当前列").textContent).toContain("quote");
    await save(); expect(screen.getByLabelText("当前列").textContent).not.toContain("quote");
  });

  it("切号关闭旧草稿，旧账号迟到响应不会覆盖新账号偏好", async () => {
    let finishOld!: (value: ColumnPreference) => void;
    vi.mocked(getPromotionColumnPreference).mockReturnValueOnce(new Promise((resolve) => { finishOld = resolve; }));
    mount();
    act(() => useAuthStore.setState({ user: { ...user, id: "user-b" } }));
    await screen.findByText("ready"); await open();
    fireEvent.click(screen.getByRole("checkbox", { name: "报价" }));
    await act(async () => finishOld({ order: ["quote"], hidden: ["stage"] }));
    expect(screen.getByLabelText("当前列").textContent).toBe("code,stage,content_category,quote");
    act(() => useAuthStore.setState({ user: { ...user, id: "user-c" } }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(savePromotionColumnPreference).not.toHaveBeenCalled();
  });
});
