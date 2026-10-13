export default {
  plugins: {
    // #122 v3→v4：Tailwind 的 PostCSS 插件独立成 @tailwindcss/postcss（v3 的直接用
    // `tailwindcss` 当插件在 v4 会直接报错）。vendor 前缀由 v4 引擎内置，autoprefixer 移除。
    "@tailwindcss/postcss": {},
  },
};
