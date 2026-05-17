/**
 * 默认兴趣偏好：AI 系统 (aisys) + MoE
 * 首次访问（localStorage 为空）时自动生效；可在 Settings 中修改并保存。
 */
const USER_PREFERENCES = {
  // GitHub Actions 变量 CATEGORIES 建议与此保持一致
  categories: ['cs.LG', 'cs.DC', 'cs.AI'],

  keywords: [
    // MoE（训练 / 推理 / 压缩 / 算法）
    'MoE',
    'mixture of experts',
    'mixture-of-experts',
    'expert parallelism',
    'expert routing',
    'sparse MoE',
    'MoE compression',
    // Serving / 推理系统
    'vLLM',
    'inference engine',
    'LLM serving',
    'inference system',
    // 分布式训练
    'FSDP',
    'tensor parallelism',
    'communication optimization',
    'distributed training',
    // 推理加速
    'speculative decoding',
    'attention optimization',
    // 显存 / KV Cache
    'KV cache',
    'memory management',
    'cache compression',
    // 编译 / 量化
    'Triton',
    'MLIR',
    'quantization',
    // AI 基础设施
    'GPU scheduling',
    'cluster management',
    'AI infra',
  ],

  authors: [],
};

function getDefaultKeywords() {
  return USER_PREFERENCES.keywords ? [...USER_PREFERENCES.keywords] : [];
}

function getDefaultAuthors() {
  return USER_PREFERENCES.authors ? [...USER_PREFERENCES.authors] : [];
}

function getStoredKeywords() {
  const saved = localStorage.getItem('preferredKeywords');
  if (saved) {
    try {
      const parsed = JSON.parse(saved);
      if (Array.isArray(parsed) && parsed.length > 0) {
        return parsed;
      }
    } catch (e) {
      console.error('解析保存的关键词失败:', e);
    }
  }
  return getDefaultKeywords();
}

function getStoredAuthors() {
  const saved = localStorage.getItem('preferredAuthors');
  if (saved) {
    try {
      const parsed = JSON.parse(saved);
      if (Array.isArray(parsed) && parsed.length > 0) {
        return parsed;
      }
    } catch (e) {
      console.error('解析保存的作者失败:', e);
    }
  }
  return getDefaultAuthors();
}
