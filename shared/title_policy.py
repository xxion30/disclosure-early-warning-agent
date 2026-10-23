"""Approved title candidates, shared by the R4 adapter and calculation boundary."""
import re


def normalized_title(title):
    title = ''.join(str(title or '').split()).replace('·', 'ㆍ')
    return re.sub(r'^(?:\[(?:기재정정|첨부정정)\])+', '', title)


def title_disposition(title, signal_type):
    """candidate/watch/timeline/reference/needs_check; never resolve an event here."""
    title = normalized_title(title)
    if any(marker in title for marker in ('(자회사의주요경영사항)', '(종속회사의주요경영사항)')):
        return 'reference'
    if signal_type == 'EMBEZZLEMENT':
        if ('진행사항' in title or '이후' in title or '조회공시' in title
                or '풍문' in title or '혐의설' in title):
            return 'timeline'
        if title and not title.startswith(('횡령ㆍ배임혐의발생', '횡령ㆍ배임사실확인',
                                           '횡령배임혐의발생', '횡령배임사실확인')):
            return 'needs_check'
    if signal_type in ('TRADING_HALT', 'DELISTING', 'ADMIN_ISSUE'):
        if '기간변경' in title and '상장폐지사유발생' in title:
            return 'needs_check'
        if any(word in title for word in ('정지해제', '기간변경', '불성실공시')):
            # Combined rumor stop/release notices describe an observation, not a confirmed RED.
            if '풍문' in title or '보도관련' in title:
                return 'watch'
            return 'timeline'
        if '풍문' in title or '보도관련' in title:
            return 'watch'
        if any(word in title for word in ('지정우려', '이의신청', '개선기간')):
            return 'timeline'
        if signal_type == 'DELISTING' and any(word in title for word in ('상장폐지관련', '상장폐지기준해당')):
            return 'timeline'
    return 'candidate'
