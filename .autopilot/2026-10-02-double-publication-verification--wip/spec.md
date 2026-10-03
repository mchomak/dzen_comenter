# Publication confirmation

Studio source comment is identified by its existing synthetic ID. The reply must match normalized text and the current DOMEO author identity. Do not reload the Studio page after submit. After a short delay, require the reply to be visible in the source thread.

Open `Comment.post_url` in a new page of the same browser context. Scroll to `[data-testid="article-comments"]`, reveal `[data-testid="show-more-comments"]` until the source comment is found or the list is exhausted, and expand hidden child replies. The source is matched by its text and author from the Studio node (prefer author href when available). Require a child reply with the same normalized text and DOMEO account identity. Close the new page, leaving the Studio page open. Handle delayed rendering with bounded polling. Distinguish Studio and public article failures in structured logs and raised errors. A network 2xx alone does not complete the job.

Evidence: the supplied Studio fragment contains the account href `/user/ajuz3gsfig4qruq5-hnc92zsydo` and full display name `DOMEO | РЕМОНТ КВАРТИР | НЕДВИЖИМОСТЬ`; article fragment has `root-comment`, `child-comments-container`, `child-comment`, `comment-author-link`; comments fragment has `show-more-comments` and a collapsed `2 ответа` button. The full article HTML is `пример статьи.html` in repo root.
