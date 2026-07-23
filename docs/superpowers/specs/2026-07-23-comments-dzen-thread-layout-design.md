# Comments Dzen Thread Layout Design

## Purpose

Make the comments feed read like the Dzen reference: a post is followed by a
compact, increasingly indented thread of comments and the bot's child reply.

## Layout

The dialogue cell contains a post block, then one tree. History messages are
chronological nodes with shallow increasing depth. The current comment follows
the history and the bot reply is one level deeper. Thin vertical connectors and
small left offsets communicate the hierarchy; cards, large borders, reactions,
and reply controls are absent.

## Fallbacks

A safe URL renders a new-tab post link. It shows the stored title when present
and `Открыть пост` for legacy rows without one. Missing or unsafe URLs show the
exact neutral placeholder `Ссылка на пост недоступна`.

## Density

All table headers are centred. The table's minimum width and dialogue-column
allocation are reduced from the stage-19 values; cell and node spacing stay at
or below 8px between adjacent messages.
