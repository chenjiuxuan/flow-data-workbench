"""初稿接口的对外错误。detail 形状固定为 {code, message}。"""


class DraftError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


FILE_TOO_LARGE = "文件超过 10 MiB，请压缩或拆分后再上传"
UNSUPPORTED_TYPE = "只支持 txt、md、docx、pdf 文件"
PARSE_FAILED = "文件已损坏或无法读取，请确认文件完整后重试"
SCANNED_PDF = "该 PDF 没有可用文本层，疑似扫描件，暂不支持识别"
EMPTY_TEXT = "没有可用文字，请粘贴内容或上传包含文字的文件"
TEXT_TOO_LONG = "文字超过 20,000 字，请删减后再生成"
NOT_A_PROCESS = "未识别到流程"
LLM_FAILED = "模型生成失败，请稍后重试"
LLM_TIMEOUT = "生成超时，请稍后重试"
LLM_NOT_CONFIGURED = "未配置语言模型，无法生成流程初稿"
BOTH_INPUTS = "请只提交文字或文件中的一种"
BAD_INPUT_KIND = "inputKind 只能是 description、minutes 或 sop"
BAD_MATCH_ASSETS = "matchAssets 只能是 true 或 false"


def not_process_message(reason: str = "") -> str:
    extra = " ".join((reason or "").split())
    if not extra or extra == NOT_A_PROCESS:
        return NOT_A_PROCESS
    return f"{NOT_A_PROCESS}：{extra[:180]}"
