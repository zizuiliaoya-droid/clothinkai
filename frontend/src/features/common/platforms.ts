// 博主 / 谈款 / 推广的平台清单（全站一份）。顺序与后端 blogger/enums.py 的 Platform 枚举一致；
// 后端博主按枚举校验，谈款 / 推广的 platform 仍是 ≤ 16 字自由文本，这里只管下拉选项。
export const PLATFORMS = ["小红书", "抖音", "快手", "B站", "得物"] as const;
export type Platform = (typeof PLATFORMS)[number];
