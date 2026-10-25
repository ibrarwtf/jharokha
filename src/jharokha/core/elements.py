"""Text-less UI elements from pixels: icons and buttons (a taskbar's apps, an app's icon
rail, a compose bar's send and attach) as boxes, for a locator such as "the icon left of
'Type a message'".

# TODO: not built. No live task has needed a text-less target yet: every live click so far
# had text, a region or a taskbar spot from apps.toml. v0.24.6 in git history has the numpy
# version (compact blobs off each pane's background, less the text OCR read, about 30 ms a
# frame) and the {"icon": true, ...} locator that used it; bring it back from there the
# first time a live task needs a button without text.
"""
