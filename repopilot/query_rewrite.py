"""Local retrieval rewrites; the original user message remains the answer prompt."""
import re
from dataclasses import dataclass


# Append search vocabulary without replacing paths, identifiers or quoted literals.
CONCEPTS = (
    (r"标题", "title"),
    (r"校验|合法性|非空|拦住|输入检查", "validate validation invalid required"),
    (r"存储|保存|写入|持久化|存哪|存到", "store save persist insert"),
    (r"笔记", "note notes"),
    (r"缓存", "cache"),
    (r"过期|失效", "stale expire invalidate"),
    (r"降级|回退|兜底", "fallback"),
    (r"检索|召回|找回", "retrieve retrieval search"),
    (r"重排|排序", "rank rerank sort"),
    (r"鉴权|认证|登录", "auth authentication login"),
    (r"重试", "retry retries"),
    (r"超时", "timeout"),
    (r"报错|错误|异常", "error exception"),
    (r"监听|端口", "listen port"),
    (r"依赖", "dependencies requirements"),
    (r"环境变量", "environ getenv"),
)
FOLLOWUP = re.compile(
    r"^(?:那(?:么)?[，, ]*)?(?:它|它们|这个(?:函数|方法|类|模块|接口|文件|流程)|上述|刚才提到的)"
    r"|^(?:(?:where|how|why|when|what)\s+(?:does|do|is|are|can)\s+(?:it|they|this|that)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QueryRewrite:
    semantic: str
    terms: tuple[str, ...]
    contextual: bool

    @property
    def lexical(self):
        return " ".join((self.semantic, *self.terms))


def rewrite_query(query, previous_question=""):
    current = query.strip()
    contextual = bool(previous_question.strip() and FOLLOWUP.search(current))
    semantic = f"{current}\n{previous_question.strip()[:240]}" if contextual else current
    terms = tuple(dict.fromkeys(word for pattern, words in CONCEPTS
                                if re.search(pattern, semantic) for word in words.split()))
    return QueryRewrite(semantic, terms, contextual)
