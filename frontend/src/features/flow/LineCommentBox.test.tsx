// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LineCommentBox } from "./LineCommentBox";

const mocks = vi.hoisted(() => ({ post: vi.fn(), readOnly: false }));
vi.mock("./api", () => ({ postLineComment: mocks.post, lineEventsKey: (id: string) => ["line-events", id] }));
vi.mock("@/stores/supportStore", () => ({ useReadOnlySupport: () => mocks.readOnly }));
beforeEach(() => { mocks.post.mockReset().mockResolvedValue({}); mocks.readOnly = false; });
afterEach(cleanup);

function mount() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><LineCommentBox lineId="line-one" /></QueryClientProvider>);
}

describe("留言框截图粘贴", () => {
  it("图片可直接粘贴进留言框，并沿用原文字加单图提交", async () => {
    mount();
    const image = new File(["synthetic"], "chat.png", { type: "image/png" });
    const input = screen.getByLabelText("留言");
    fireEvent.change(input, { target: { value: "已沟通" } });
    expect(fireEvent.paste(input, { clipboardData: { files: [image], items: [] } })).toBe(false);
    expect(screen.getByText("chat.png")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("line-one", { text: "已沟通", image }));
    await waitFor(() => expect(screen.queryByText("chat.png")).toBeNull());
  });

  it("纯文本粘贴保持浏览器默认行为", () => {
    mount();
    expect(fireEvent.paste(screen.getByLabelText("留言"), { clipboardData: { files: [], items: [] } })).toBe(true);
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("排查会话没有可写贴图入口，也不能发送留言", () => {
    mocks.readOnly = true;
    mount();
    expect(screen.queryByRole("group", { name: "截图上传区" })).toBeNull();
    expect((screen.getByLabelText("留言") as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: /发\s*送/ }) as HTMLButtonElement).disabled).toBe(true);
  });
});
