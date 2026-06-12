# Broker logo templates

This directory holds per-firm broker logo templates used by `overlay.detect_logos`
for `cv2.matchTemplate` against flattened OM rasters. It ships empty.

Add one grayscale PNG per broker firm, named by firm slug (for example
`marcus-millichap.png`). Each template should be a tight crop of the firm's mark
as it appears composited on OM photos. Templates must be smaller than the photos
they match against.

Do not fabricate or download logos into this directory speculatively. Add a
template only when a real firm's mark is licensed for this use and confirmed to
appear on that firm's OM photos. With no templates present, logo detection is a
no-op and the other overlay detectors carry the work.
