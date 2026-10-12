# aws-blog-mcp

[![PyPI](https://img.shields.io/pypi/v/aws-blog-mcp.svg)](https://pypi.org/project/aws-blog-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/aws-blog-mcp.svg)](https://pypi.org/project/aws-blog-mcp/)
[![CI](https://github.com/anhyobin/aws-blog-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/anhyobin/aws-blog-mcp/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/anhyobin/aws-blog-mcp/blob/main/LICENSE)

[English](https://github.com/anhyobin/aws-blog-mcp/blob/main/README.md) | **한국어**

> **비공식 프로젝트입니다.** 이 프로젝트는 개인이 만든 독립적인 커뮤니티 프로젝트로, Amazon Web
> Services(AWS)와 제휴 관계가 없으며 AWS의 보증이나 후원을 받지 않습니다. "AWS"와 "Amazon Web
> Services"는 Amazon.com, Inc. 또는 그 계열사의 상표입니다.
>
> 블로그 콘텐츠의 저작권은 Amazon Web Services, Inc. 또는 그 계열사(또는 각 글의 저자)에 있습니다.
> 이 서버는 사용자가 요청할 때마다 aws.amazon.com에서 글을 가져올 뿐, 글을 패키지에 포함하거나
> 재배포하거나 디스크에 저장하지 않습니다. 글을 활용할 때는 출처를 밝히고 원문 링크를 함께
> 남겨 주세요.

<!-- mcp-name: io.github.anhyobin/aws-blog-mcp -->

AI 어시스턴트가 [AWS 기술 블로그(AWS Korea tech blog)](https://aws.amazon.com/ko/blogs/tech/)를
검색하고 읽을 수 있게 해 주는 [MCP](https://modelcontextprotocol.io/) 서버입니다. AWS 기술
블로그는 AWS 솔루션즈 아키텍트와 고객이 쓴 기술 심층 분석, 고객 사례, 따라 하기 가이드를
한국어로 제공하는 블로그입니다.

## 왜 만들었나요?

[AWS Documentation MCP Server](https://github.com/awslabs/mcp/tree/main/src/aws-documentation-mcp-server)를
쓰면 어시스턴트가 AWS 공식 문서를 참고할 수 있지만, 국가별 블로그까지는 다루지 않습니다.
마이그레이션 경험담, 아키텍처 해설, 국내 고객 사례처럼 한국어로 된 실전 지식은 AWS 기술
블로그에만 있는 경우가 많습니다. 게다가 블로그 목록 뒤에 있는 AWS 디렉터리 API는 단어 단위로만
일치 여부를 판단하기 때문에, 조사가 붙거나(`쿠버네티스를`) 복합어로 된 한국어 검색어로는 대부분의
글을 찾지 못합니다. 이 서버를 쓰면 Claude Code, Claude Desktop, Kiro, Cursor, VS Code를 비롯한
모든 MCP 클라이언트에서 이 블로그를 검색하고 읽을 수 있습니다.

다른 AWS 블로그도 추가할 수 있도록 설계했습니다. 블로그 정보는 작은 레지스트리
(`src/aws_blog_mcp/blogs.py`)에 정의되어 있으며, 현재는 AWS 기술 블로그만 지원합니다.

## 주요 기능

- **한국어를 고려한 키워드 검색**: 글 제목, 요약, 카테고리와 태그, URL 슬러그, 저자 이름을
  대상으로 검색합니다. 한글은 부분 문자열로 일치시키고, 흔한 조사와 어미를 떼어 내며
  (`쿠버네티스를`로 검색해도 `쿠버네티스`와 일치), 한영 동의어를 함께 찾습니다(`쿠버네티스` =
  `Kubernetes` = `k8s`, `베드락` = `Bedrock` 등).
- **필터**: 카테고리(하위 카테고리 포함), 저자, 게시일로 거르고 페이지 단위로 넘겨 볼 수 있습니다.
- **카테고리 탐색**: 블로그의 카테고리 트리와 카테고리별 글 수를 보여 주며, 그대로 필터로 쓸 수
  있습니다.
- **깔끔한 마크다운으로 글 읽기**: 제목, 목록, 표, 이미지, 코드 블록(언어 정보 포함)을 그대로
  살리고, WordPress 잔여물과 페이지 위젯은 걷어 냅니다. 긴 글은 여러 조각으로 나누어 돌려주며,
  가능하면 코드 블록 바깥의 문단 경계에서 자릅니다.
- **예의 바른 동작**: 출처를 알 수 있는 User-Agent, 클라이언트 측 요청 간격 조절, 횟수가 제한된
  재시도를 사용하며 백그라운드 크롤링은 하지 않습니다.
- **안전한 기본값**: 모든 도구는 읽기 전용이고, 네트워크 접근은 `https://aws.amazon.com`으로
  제한되며, 응답 크기에 상한이 있고, 가져온 콘텐츠는 신뢰할 수 없는 참고 자료로 표시합니다.

## 도구

모든 도구는 읽기 전용입니다(`readOnlyHint: true`, `openWorldHint: true`). 날짜는 한국 시간
(UTC+9) 기준 날짜입니다.

### `search_posts`

한국어 또는 영어 키워드로 블로그를 검색합니다. 결과에 나오는 글은 모든 검색어를 포함합니다
(`방법`, `관련`, `aws` 같은 군더더기 단어는 제외하고 판단합니다). 모든 검색어를 포함하는 글이
없으면 일부 검색어와 일치하는 글을 대신 돌려주며, 이때 `match` 값은 `any_term`입니다. 이 경우에는
드물고 변별력 있는 검색어일수록 순위에 더 크게 반영됩니다.

| 파라미터 | 타입 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `query` | string, 필수 (1-200자) | | 키워드. 예: `EKS 오토스케일링`, `Bedrock AgentCore`, `게임 서버 마이그레이션`. 짧은 명사가 가장 잘 맞습니다. |
| `category` | string (최대 200자) | 없음 | 이 카테고리나 그 하위 카테고리의 글만 찾습니다. `list_categories`에서 얻은 이름, id 또는 경로. 예: `Amazon Bedrock`, `artificial-intelligence/kiro`. 카테고리가 아니라 글 태그나 AWS 제품 라벨인 값(예: `EKS`)이면 그 라벨로 거르고, `notice`에서 그 사실과 관련 카테고리를 알려 줍니다. |
| `author` | string (최대 100자) | 없음 | 저자 이름에 이 텍스트가 들어간 글만 찾습니다. |
| `published_after` | 날짜 `YYYY-MM-DD` | 없음 | 이 날짜 또는 그 이후에 게시된 글만 찾습니다. |
| `published_before` | 날짜 `YYYY-MM-DD` | 없음 | 이 날짜 또는 그 이전에 게시된 글만 찾습니다. |
| `sort` | `relevance` \| `newest` | `relevance` | 관련도순 또는 최신순. |
| `limit` | 정수 1-50 | `10` | 돌려받을 최대 글 수. |
| `offset` | 정수 >= 0 | `0` | 건너뛸 결과 수(페이지 넘김용). |

구조화된 결과를 돌려줍니다(텍스트 콘텐츠에도 같은 데이터가 압축된 JSON으로 들어 있음): `blog`,
`query`, `match`(`all_terms` 또는 `any_term`), `total`,
`offset`, `next_offset`(마지막 페이지면 `null`), `notice`(참고 사항이 있을 때), `posts`. `posts`의
각 항목에는 `id`(예: `65-28623`), `title`, `url`, `published`, `authors`, `categories`,
`excerpt`가 들어 있습니다.

### `list_recent_posts`

글을 최신순으로 나열합니다. 새로 올라온 글을 확인하거나, 키워드 없이 카테고리, 저자, 기간별로
둘러볼 때 사용합니다.

| 파라미터 | 타입 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `category` | string (최대 200자) | 없음 | `search_posts`와 같습니다. |
| `author` | string (최대 100자) | 없음 | `search_posts`와 같습니다. |
| `published_after` | 날짜 `YYYY-MM-DD` | 없음 | `search_posts`와 같습니다. |
| `published_before` | 날짜 `YYYY-MM-DD` | 없음 | `search_posts`와 같습니다. |
| `limit` | 정수 1-50 | `10` | 돌려받을 최대 글 수. |
| `offset` | 정수 >= 0 | `0` | 건너뛸 결과 수(페이지 넘김용). |

`blog`, `total`, `offset`, `next_offset`, `notice`, `posts`(필드는 위와 같음)를 돌려줍니다.

### `list_categories`

블로그 글에 쓰인 카테고리를 많이 쓰인 순서로 나열합니다. 주제(예: Artificial Intelligence >
Amazon Bedrock), 글 유형(Customer Solutions, Technical How-to), 난이도(Advanced (300))가 모두
카테고리에 포함됩니다.

| 파라미터 | 타입 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `query` | string (최대 100자) | 없음 | 이름이나 id에 이 텍스트가 들어간 카테고리만 나열합니다. 예: `bedrock`, `game`, `쿠버네티스`(한/영 동의어도 찾음). |
| `parent` | string (최대 200자) | 없음 | 이 카테고리(이름 또는 id)의 하위 카테고리만 나열합니다. |
| `limit` | 정수 1-500 | `50` | 돌려받을 최대 카테고리 수. |

`blog`, `total`, `notice`, `categories`를 돌려줍니다. `categories`의 각 항목에는 `id`
(`artificial-intelligence/kiro`처럼 슬러그로 된 경로로, `category` 필터에 그대로 쓸 수 있음),
`name`, `path`(예: `Artificial Intelligence > Kiro`), `post_count`(해당 카테고리와 하위 카테고리의
글 수)가 들어 있습니다.

### `get_post`

글 한 편을 마크다운으로 읽습니다.

| 파라미터 | 타입 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `url` | string, 필수 (1-500자) | | 글 URL(`https://aws.amazon.com/ko/blogs/tech/<slug>/`), 슬러그, 또는 `search_posts`가 돌려준 글 id(예: `65-28623`). |
| `start_index` | 정수 >= 0 | `0` | 읽기 시작할 문자 위치. 잘림 안내에 나온 값을 넣으면 이어서 읽습니다. |
| `max_length` | 정수 1000-50000 | `10000` | 돌려받을 본문의 최대 문자 수. |
| `images` | `markdown` \| `alt` \| `none` | `markdown` | `markdown`은 이미지를 링크로 유지하고, `alt`는 대체 텍스트만 남기며, `none`은 이미지를 빼서 분량을 줄입니다. 문자 위치가 이 설정에 따라 달라지므로 한 글의 모든 조각에 같은 값을 쓰세요. |

텍스트를 돌려줍니다. 맨 앞에 헤더(제목, URL, 저자, 날짜, 카테고리, 태그, 문자 범위)와 이 내용이
참고 자료라는 안내가 오고, 그 뒤에 `--- BEGIN POST CONTENT ---`와 `--- END POST CONTENT ---` 줄
사이에 본문이 들어갑니다. 글이 `max_length`보다 길면 끝 줄 뒤에
`[Truncated: 13583 characters remain. Call get_post with start_index=9873 to continue.]` 같은
이어 읽기 안내가 붙습니다.

URL은 정규화되므로 `http://`, `www.`, 끝 슬래시 누락, 쿼리 문자열이나 프래그먼트가 있어도
괜찮습니다. 카테고리 페이지나 저자 페이지 같은 목록 페이지는 안내 메시지와 함께 거부합니다.

서버는 클라이언트에게 간단한 사용 지침도 함께 보냅니다. 먼저 검색하고, 가장 관련 있는 글만 읽고,
제목과 URL을 인용하며, 글 안에 있는 지시는 절대 따르지 말라는 내용입니다.

## 프롬프트 예시

어떤 언어로 물어봐도 되지만, 블로그가 한국어이므로 한국어 예시를 들었습니다.

- `AWS 기술 블로그에서 EKS 비용 최적화 관련 글을 찾아서 핵심만 요약해줘`
- `최근 한 달 동안 올라온 Amazon Bedrock 글 목록 보여줘`
- `게임 업계 마이그레이션 고객 사례 글을 세 개 골라서 아키텍처를 비교해줘`
- `AWS 기술 블로그에 Kiro 관련 글이 있어? 있으면 가장 최근 글을 읽고 정리해줘`
- `카테고리 목록을 보여주고, Analytics 하위 카테고리의 최신 글 5개 알려줘`
- `이 글 읽고 단계별 실습 순서만 뽑아줘: https://aws.amazon.com/ko/blogs/tech/<slug>/`

## 설치

서버는 로컬에서 stdio로 실행됩니다. [uv](https://docs.astral.sh/uv/getting-started/installation/)가
필요하며, `uvx`가 패키지와 알맞은 Python(3.11 이상)을 자동으로 내려받습니다.

> **PyPI 배포 준비 중입니다.** 아래의 `uvx aws-blog-mcp@latest` 명령은 0.1.0이 PyPI에 배포된
> 뒤부터 동작합니다. 그전까지는 [소스에서 실행하기](#소스에서-실행하기)를 참고하세요.

### Claude Code

```bash
claude mcp add --scope user aws-blog -- uvx aws-blog-mcp@latest
```

### Claude Desktop

`claude_desktop_config.json`에 아래 내용을 추가하고 Claude Desktop을 다시 시작합니다(Settings >
Developer > Edit Config에서 열 수 있으며, macOS에서는
`~/Library/Application Support/Claude/claude_desktop_config.json`, Windows에서는
`%APPDATA%\Claude\claude_desktop_config.json`에 있습니다).

```json
{
  "mcpServers": {
    "aws-blog": {
      "command": "uvx",
      "args": ["aws-blog-mcp@latest"]
    }
  }
}
```

Claude Desktop이 `uvx`를 찾지 못하면 `command`에 절대 경로(`which uvx`의 출력, Windows에서는
`where uvx`의 출력)를 넣으세요.

### Kiro

`~/.kiro/settings/mcp.json`(모든 워크스페이스) 또는 `.kiro/settings/mcp.json`(특정 워크스페이스)에
서버를 추가합니다.

```json
{
  "mcpServers": {
    "aws-blog": {
      "command": "uvx",
      "args": ["aws-blog-mcp@latest"]
    }
  }
}
```

### Cursor

`~/.cursor/mcp.json`(전역) 또는 `.cursor/mcp.json`(특정 프로젝트)에 서버를 추가합니다.

```json
{
  "mcpServers": {
    "aws-blog": {
      "command": "uvx",
      "args": ["aws-blog-mcp@latest"]
    }
  }
}
```

### VS Code

워크스페이스의 `.vscode/mcp.json`에 서버를 추가합니다(명령 팔레트에서 **MCP: Add Server**를
실행해도 됩니다). VS Code는 `mcpServers`가 아니라 `servers` 키를 쓴다는 점에 주의하세요.

```json
{
  "servers": {
    "aws-blog": {
      "type": "stdio",
      "command": "uvx",
      "args": ["aws-blog-mcp@latest"]
    }
  }
}
```

### 소스에서 실행하기

```bash
git clone https://github.com/anhyobin/aws-blog-mcp.git
cd aws-blog-mcp
uv sync
```

그런 다음 `uvx` 대신 클론한 디렉터리를 가리키도록 클라이언트를 설정합니다.

```bash
claude mcp add --scope user aws-blog -- uv --directory /path/to/aws-blog-mcp run aws-blog-mcp
```

JSON 설정 파일에서는 다음과 같이 씁니다.

```json
{
  "mcpServers": {
    "aws-blog": {
      "command": "uv",
      "args": ["--directory", "/path/to/aws-blog-mcp", "run", "aws-blog-mcp"]
    }
  }
}
```

`uvx aws-blog-mcp@latest --version`(소스에서는 `uv run aws-blog-mcp --version`)으로 버전을 확인할
수 있습니다.

## 설정

두 환경 변수 모두 선택 사항입니다.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `AWS_BLOG_MCP_LOG_LEVEL` | `WARNING` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` 중 하나. 로그는 stdout이 아니라 stderr로 출력됩니다. 알 수 없는 값이면 `WARNING`을 씁니다. |
| `AWS_BLOG_MCP_USER_AGENT` | `aws-blog-mcp/<version> (+https://github.com/anhyobin/aws-blog-mcp)` | aws.amazon.com에 보내는 모든 요청의 User-Agent. |

Claude Code에서는 예를 들어 다음과 같이 설정합니다.

```bash
claude mcp add --scope user -e AWS_BLOG_MCP_LOG_LEVEL=INFO aws-blog -- uvx aws-blog-mcp@latest
```

JSON 설정 파일에서는 `args` 옆에 `"env": {"AWS_BLOG_MCP_LOG_LEVEL": "INFO"}`를 추가하면 됩니다.

## 동작 방식

1. **인덱스.** 첫 도구 호출 때 aws.amazon.com 블로그 목록을 제공하는 디렉터리 API
   (`https://aws.amazon.com/api/dirs/items/search`)에서 모든 글의 메타데이터(제목, 요약, 저자,
   날짜, 카테고리, 태그)를 불러와 메모리에 보관합니다. 이후 검색, 필터링, 목록 조회는 모두 이
   인덱스를 대상으로 로컬에서 처리합니다.
2. **필요할 때만 갱신.** 인덱스를 불러온 지 10분이 지나면 다음 도구 호출 때 최근에 수정된 글을
   먼저 가져와 병합하고, 6시간마다 전체 목록을 다시 불러옵니다(삭제된 글은 이때만 사라집니다).
   갱신하는 동안 다른 호출은 기다리지 않고 기존 목록을 씁니다. 갱신이 실패하거나 30초를 넘기면
   기존 목록을 그대로 쓰면서 `notice`로 알려 주고, 1분 뒤에 다시 시도합니다. 맨 처음 불러오기가
   실패하면 15초 동안은 같은 오류를 돌려준 뒤 다시 시도합니다.
3. **글 읽기.** `get_post`는 요청을 받은 시점에 aws.amazon.com에서 글 페이지를 가져와 블로그 페이지
   템플릿에서 본문을 추출하고, 정리한 뒤 마크다운으로 변환합니다(큰 페이지가 다른 요청을 막지 않도록
   별도 스레드에서 처리). 파싱한 페이지는 메모리에 캐시하므로(최대 32페이지, 1시간) 긴 글의 다음
   조각을 읽을 때 다시 내려받지 않습니다.
4. **예의와 안전.**
   - 모든 요청에 이 프로젝트 링크가 담긴, 출처를 알 수 있는 User-Agent를 붙입니다.
   - 요청 시작 간격을 최소 0.25초로 유지합니다(서버 프로세스당 초당 최대 4건).
   - 네트워크 오류, 시간 초과, HTTP 429, 5xx 응답만 재시도합니다. 총 3번까지 시도하고, 지수 백오프를
     적용하며, `Retry-After`는 10초까지만 따릅니다(그보다 길면 기다리지 않고 실패로 처리합니다).
     한 번의 시도(리다이렉트와 본문 다운로드 포함)는 45초로 제한합니다.
   - 백그라운드에서 돌아가는 작업이 없습니다. 도구 호출에 응답하는 동안에만 요청을 보냅니다.
   - 리다이렉트(최대 3번)를 포함해 기본 포트에 인증 정보가 없는 `https://aws.amazon.com` URL만
     가져오므로, 도구 호출로 서버가 다른 호스트에 요청하게 만들 수 없습니다.
   - 응답 본문 크기에 상한이 있습니다(글 페이지 8 MB, 디렉터리 API 응답 64 MB).
   - 디스크에는 아무것도 쓰지 않습니다.

## 한계

- **메타데이터만 검색합니다.** `search_posts`는 제목, 요약, 카테고리와 태그, 슬러그, 저자 이름만
  보고, 본문 전체는 검색하지 않습니다. 본문 깊숙이에서만 언급되는 주제는 찾지 못할 수 있으니,
  더 넓은 키워드나 카테고리 필터, `list_recent_posts`를 활용하세요.
- **한국어 처리는 휴리스틱입니다.** 형태소 분석기를 쓰지 않습니다. 한글을 부분 문자열로 일치시키고
  정해진 조사·어미 목록을 떼어 내는 방식이라, 가끔 너무 많이 또는 너무 적게 일치할 수 있습니다.
  한영 동의어 표도 규모가 작고 직접 관리합니다.
- **문서화되지 않은 외부 의존성.** 디렉터리 API는 공개 문서가 없고, 글 페이지의 HTML 템플릿도
  예고 없이 바뀔 수 있습니다. 둘 중 하나라도 바뀌면 서버를 업데이트하기 전까지 검색이나 읽기가
  동작하지 않을 수 있습니다. 이를 감지하기 위해 실제 사이트를 대상으로 한 라이브 테스트를 정기적으로
  실행합니다.
- **현재는 블로그 하나만 지원합니다.** AWS 기술 블로그(`https://aws.amazon.com/ko/blogs/tech/`)만
  지원합니다.
- **반영 지연.** 새 글이나 수정된 글이 나타나기까지 최대 10분 정도 걸릴 수 있고, 삭제된 글은 최대
  6시간 동안 남아 있을 수 있습니다. 서버를 시작한 뒤 첫 호출은 인덱스를 불러오느라 조금 더
  오래 걸립니다.
- `https://aws.amazon.com`에 접속할 수 있는 **네트워크 환경**이 필요합니다.

## 개발

```bash
uv sync                          # 개발 의존성을 포함해 .venv 생성
uv run ruff format --check       # 포맷 검사
uv run ruff check                # 린트
uv run pyright                   # 타입 검사
uv run pytest                    # 오프라인 테스트 (가짜 aws.amazon.com 사용)
uv run pytest -m live --run-live # 실제 사이트를 대상으로 하는 선택형 테스트
```

다른 AWS 블로그를 추가하는 방법을 포함한 전체 개발 흐름은
[CONTRIBUTING.md](https://github.com/anhyobin/aws-blog-mcp/blob/main/CONTRIBUTING.md)를, 취약점
제보 방법은 [SECURITY.md](https://github.com/anhyobin/aws-blog-mcp/blob/main/SECURITY.md)를
참고하세요.

## 라이선스

코드는 [Apache License 2.0](https://github.com/anhyobin/aws-blog-mcp/blob/main/LICENSE)으로
배포됩니다. [NOTICE](https://github.com/anhyobin/aws-blog-mcp/blob/main/NOTICE)도 함께 참고하세요.
이 라이선스는 이 소프트웨어에만 적용되며, 소프트웨어가 가져오는 블로그 콘텐츠에는 적용되지
않습니다. 블로그 콘텐츠의 권리는 원래 소유자에게 있습니다.
