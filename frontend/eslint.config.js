import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    plugins: { "react-hooks": reactHooks, "react-refresh": reactRefresh },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
    },
  },
  {
    // 基座建好之后，「有没有人绕过基座」不能靠人眼 review。
    // 阶段 2-4 期间页面还没迁完，设成 error 会让 lint 一直红——红久了就没人看，
    // 等于没有。**2026-09-03 阶段 5 收尾，最后 6 处（4 张表格 + 2 个复选框）已收口，
    // 命中数归零，按 spec 的约定提为 error。** 从这一刻起绕过基座会让 lint 退出码非 0。
    files: ["src/components/**/*.tsx"],
    ignores: ["src/components/ui/**"], // 基座内部必须写裸元素
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXOpeningElement[name.name='table']",
          message: "表格请用 components/ui/DataTable，它保证行高、分隔线、列宽和空态一致。",
        },
        {
          // 用 :has 而不是 attributes.0——把 type 硬编码成第 0 个属性，
          // 换个书写顺序规则就静默失效。Step 3 要求实际看到 warning 输出，
          // 若这版 esquery 不支持 :has，那一步会当场暴露。
          selector:
            "JSXOpeningElement[name.name='input']:has(JSXAttribute[name.name='type'][value.value='checkbox'])",
          message: "复选框请用 components/ui/Checkbox，原生 checkbox 无法声明式表达 indeterminate。",
        },
        {
          selector: "JSXOpeningElement[name.name='button']",
          message: "按钮请用 components/ui/Button，它保证禁用必有可见原因（CLAUDE.md 第一条）。",
        },
      ],
    },
  },
);
