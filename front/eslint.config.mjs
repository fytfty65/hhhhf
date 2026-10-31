import js from '@eslint/js';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import tseslint from 'typescript-eslint';

/**
 * 真正的 ESLint 门禁，取代 front/scripts/lint.mjs（那个脚本只查 debugger 和硬编码
 * localhost，warning 还不阻断，等于没有可访问性检查）。
 *
 * 分层策略：
 * - 全部 TS/TSX 走 typescript-eslint 语法解析；
 * - jsx-a11y recommended 覆盖"按钮没名字、图片没 alt、aria 属性写错"这类会直接
 *   影响可用性的规则，作为错误阻断 CI；
 * - 仍在清理中的两条交互规则降为 warn：项目里有大量既有 onClick div，一次性
 *   改成可聚焦控件风险更大，先在 CI 里持续可见，再逐屏替换；
 * - 禁止业务代码直接访问服务地址与裸 fetch，把请求强制收敛到 lib/apiClient。
 */
export default tseslint.config(
  {
    ignores: [
      'node_modules/**',
      '.next/**',
      'out/**',
      'public/**',
      'playwright-report/**',
      'test-results/**',
      'next-env.d.ts',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  jsxA11y.flatConfigs.recommended,
  {
    files: ['**/*.{ts,tsx,js,jsx,mjs}'],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'module',
    },
    rules: {
      // TypeScript 自己就在做未定义标识符检查，ESLint 的 no-undef 在 TS 里只会误报 DOM 全局。
      'no-undef': 'off',
      'no-empty': ['error', { allowEmptyCatch: true }],
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none' },
      ],
      '@typescript-eslint/no-require-imports': 'off',
      // 交互容器已全部改为原生 button 或 role="button" + 键盘行为，
      // 这两条从 warn 提升为 error，防止回退成"只能鼠标点"的 div。
      'jsx-a11y/click-events-have-key-events': 'error',
      'jsx-a11y/no-static-element-interactions': 'error',
    },
  },
  {
    // 业务界面不得自己拼服务地址或直连 fetch：认证、超时、错误码、重试、
    // 追踪都必须经过统一客户端。
    files: ['app/**/*.{ts,tsx}'],
    ignores: ['app/lib/**', 'app/api/**'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector: "CallExpression[callee.name='fetch']",
          message: '请使用 lib/apiClient 的 apiRequest / apiFetch，不要直接调用 fetch。',
        },
        {
          selector: "ImportSpecifier[imported.name='API_BASE']",
          message: '业务组件不得直接使用服务地址，请改用 apiRequest 或 assetUrl。',
        },
      ],
      'no-restricted-imports': [
        'error',
        {
          paths: [
            {
              name: '@/app/lib/utils',
              importNames: ['API_BASE'],
              message: '业务组件不得直接使用服务地址，请改用 apiRequest 或 assetUrl。',
            },
          ],
        },
      ],
    },
  },
  {
    // 测试与脚本不受"业务组件"约束。
    files: ['**/*.test.ts', '**/*.test.tsx', 'e2e/**', 'scripts/**', '*.config.{ts,js,mjs}'],
    rules: {
      'no-restricted-syntax': 'off',
      'no-restricted-imports': 'off',
      // 一次性探针脚本允许用 @ts-ignore 压制类型，不进入产品代码路径。
      '@typescript-eslint/ban-ts-comment': 'off',
    },
  },
);
