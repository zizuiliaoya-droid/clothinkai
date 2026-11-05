// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { message } from "antd";
import { pastedImages, ScreenshotUpload } from "./ScreenshotUpload";

const support = vi.hoisted(() => ({ readOnly: false }));
vi.mock("@/stores/supportStore", () => ({ useReadOnlySupport: () => support.readOnly }));

beforeEach(() => {
  support.readOnly = false;
  vi.spyOn(message, "error").mockImplementation(() => ({}) as ReturnType<typeof message.error>);
  vi.spyOn(message, "info").mockImplementation(() => ({}) as ReturnType<typeof message.info>);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const png = (name = "screen.png") => new File(["synthetic-image"], name, { type: "image/png" });
const clipboard = (files: File[]) => ({ files, items: [], types: files.length ? ["Files"] : ["text/plain"] });

describe("截图选择、粘贴与拖拽", () => {
  it("粘贴只交给接收事件的上传区，普通页面不注册全局监听", () => {
    const first = vi.fn(), second = vi.fn();
    render(<><ScreenshotUpload value={null} onChange={first} /><ScreenshotUpload value={null} onChange={second} /><textarea aria-label="其他输入" /></>);
    const image = png();
    expect(fireEvent.paste(screen.getByLabelText("其他输入"), { clipboardData: clipboard([image]) })).toBe(true);
    expect(first).not.toHaveBeenCalled();
    expect(second).not.toHaveBeenCalled();
    expect(fireEvent.paste(screen.getAllByRole("group")[0], { clipboardData: clipboard([image]) })).toBe(false);
    expect(first).toHaveBeenCalledTimes(1);
    expect(first).toHaveBeenCalledWith(image);
    expect(second).not.toHaveBeenCalled();
  });

  it("纯文本粘贴不会被阻止", () => {
    const onChange = vi.fn();
    render(<ScreenshotUpload value={null} onChange={onChange} />);
    expect(fireEvent.paste(screen.getByRole("group"), { clipboardData: clipboard([]) })).toBe(true);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("一次多张只取第一张并提示", () => {
    const onChange = vi.fn();
    render(<ScreenshotUpload value={null} onChange={onChange} />);
    const images = [png("one.png"), png("two.png")];
    fireEvent.paste(screen.getByRole("group"), { clipboardData: clipboard(images) });
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(images[0]);
    expect(message.info).toHaveBeenCalledWith("此处只支持一张图片，已取第一张");
  });

  it("拖入的文件使用相同选择逻辑", () => {
    const onChange = vi.fn();
    render(<ScreenshotUpload value={null} onChange={onChange} />);
    const file = png();
    expect(fireEvent.dragOver(screen.getByRole("group"), { dataTransfer: clipboard([file]) })).toBe(false);
    expect(fireEvent.drop(screen.getByRole("group"), { dataTransfer: clipboard([file]) })).toBe(false);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(file);
  });

  it.each([
    ["类型", () => new File(["not-image"], "notes.pdf", { type: "application/pdf" })],
    ["大小", () => new File([new Uint8Array(10 * 1024 * 1024 + 1)], "large.png", { type: "image/png" })],
  ])("拖入错误%s的文件不能替换已选图片", (_reason, file) => {
    const onChange = vi.fn();
    render(<ScreenshotUpload value={png("keep.png")} onChange={onChange} />);
    fireEvent.drop(screen.getByRole("group"), { dataTransfer: clipboard([file()]) });
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText("keep.png")).toBeTruthy();
    expect(message.error).toHaveBeenCalledOnce();
  });

  it("沿用入口自带的校验规则", () => {
    const onChange = vi.fn(), validate = vi.fn(() => "该入口不接受此图片");
    render(<ScreenshotUpload value={null} onChange={onChange} validate={validate} />);
    const file = png();
    fireEvent.paste(screen.getByRole("group"), { clipboardData: clipboard([file]) });
    expect(validate).toHaveBeenCalledTimes(1);
    expect(validate).toHaveBeenCalledWith(file);
    expect(onChange).not.toHaveBeenCalled();
    expect(message.error).toHaveBeenCalledWith("该入口不接受此图片");
  });

  it("选择文件后显示文件名，移除后清空", async () => {
    function Picker() {
      const [value, setValue] = useState<File | null>(null);
      return <ScreenshotUpload value={value} onChange={setValue} />;
    }
    const { container } = render(<Picker />);
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [png()] } });
    expect(await screen.findByText("screen.png")).toBeTruthy();
    fireEvent.click(screen.getByTitle("Remove file"));
    await waitFor(() => expect(screen.queryByText("screen.png")).toBeNull());
  });

  it("禁用入口不接收粘贴或拖拽", () => {
    const onChange = vi.fn();
    render(<ScreenshotUpload value={null} onChange={onChange} disabled />);
    const group = screen.getByRole("group");
    fireEvent.paste(group, { clipboardData: clipboard([png()]) });
    fireEvent.drop(group, { dataTransfer: clipboard([png()]) });
    expect(onChange).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: /选择截图/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("平台只读排查不显示上传入口", () => {
    support.readOnly = true;
    render(<ScreenshotUpload value={null} onChange={vi.fn()} />);
    expect(screen.queryByRole("group")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("兼容只提供 clipboard items 的浏览器", () => {
    const file = png();
    expect(pastedImages({ files: [], items: [{ kind: "file", type: "image/png", getAsFile: () => file }] } as unknown as DataTransfer)).toEqual([file]);
  });
});
