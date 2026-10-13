"""初稿接口错误。

detail 为 {code, message}。not_a_process 可另带 reason，不写入 message。
code：file_too_large 413，unsupported_type 415，invalid_input / parse_failed /
scanned_pdf / empty_text / text_too_long / not_a_process 422，
llm_not_configured 503，llm_failed 502，llm_timeout 504。
"""


class DraftError(Exception):
    def __init__(self, status: int, code: str, message: str, reason: str | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.reason = reason
        super().__init__(message)


FILE_TOO_LARGE = "文件超过 10 MiB，请压缩或拆分后再上传"
UNSUPPORTED_TYPE = "只支持 txt、md、docx、pdf 文件"
PARSE_FAILED = "文件已损坏或无法读取，请确认文件完整后重试"
SCANNED_PDF = "这个 PDF 像是扫描件，读不到文字。请把里面的文字复制进来，或上传 Word/文本版"
EMPTY_TEXT = "没有可用文字，请粘贴内容或上传包含文字的文件"
TEXT_TOO_LONG = "文字超过 20,000 字，请删减后再生成"
NOT_A_PROCESS = "没有识别到流程。请描述谁在什么环节做什么、有哪些判断，或者直接贴会议纪要或 SOP 原文"
LLM_FAILED = "模型生成失败，请稍后重试"
LLM_TIMEOUT = "生成超时，请稍后重试"
LLM_NOT_CONFIGURED = "未配置语言模型，无法生成流程初稿。请联系管理员配置模型"
INVALID_BOTH = "请只粘贴文字，或只上传一个文件"
INVALID_NEITHER = "请粘贴文字或上传一个文件"
INVALID_KIND = "请选择业务描述、会议纪要或 SOP"
INVALID_MATCH = "请选择是否匹配已有资产：开启或关闭"
