import { describe, expect, it } from "vitest";
import { receiverInitial, receiverPatch, receiverPhoneError } from "./receiverForm";

const initial = receiverInitial({
  receiver_name: "张三",
  receiver_phone: "13800000000",
  receiver_address: null,
});

describe("receiverPatch", () => {
  it("没改的不交，改了的交去空白后的值", () => {
    expect(
      receiverPatch(initial, {
        receiver_name: " 张三 ",
        receiver_phone: "13900000000",
        receiver_address: "",
      })
    ).toEqual({ receiver_phone: "13900000000" });
  });

  it("清空给 null，原来空的填上就交", () => {
    expect(
      receiverPatch(initial, { receiver_name: "  ", receiver_phone: "13800000000", receiver_address: "上海" })
    ).toEqual({ receiver_name: null, receiver_address: "上海" });
  });

  it("表单里没这一项（undefined）按空串比", () => {
    expect(receiverPatch(receiverInitial({ receiver_name: null, receiver_phone: null, receiver_address: null }), {})).toEqual({});
  });
});

describe("receiverPhoneError", () => {
  it("只认 INVALID_RECEIVER_PHONE", () => {
    expect(
      receiverPhoneError({ response: { data: { code: "INVALID_RECEIVER_PHONE", message: "电话不对" } } })
    ).toBe("电话不对");
    expect(receiverPhoneError({ response: { data: { code: "VALIDATION_ERROR", message: "x" } } })).toBeNull();
    expect(receiverPhoneError(null)).toBeNull();
  });
});
