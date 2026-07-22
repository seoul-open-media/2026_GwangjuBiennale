"""
PlatformIO monitor filter: XBee 수신 라인만 출력
[XBEE ...] 와 [CMD] 로 시작하는 라인만 표시
"""
import re
from platformio.public import DeviceMonitorFilterBase

class XBeeOnly(DeviceMonitorFilterBase):
    NAME = "xbee_only"

    def __init__(self):
        self.buf = ""

    def rx(self, text):
        self.buf += text
        out = ""
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if re.search(r'\[XBEE|\[CMD\]', line):
                out += line + "\n"
        return out
