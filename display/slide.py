# Dragging between pages: the page under the finger and the one it reveals,
# side by side, shifted by how far the finger has moved.
#
# Both pages are drawn once, into their own copies of the screen, when the
# drag starts. Each frame after that is just this copy - a few milliseconds in
# viper - and sending the page's rows. Drawing a page takes 0.1-0.4 s, so it
# could never keep up with a finger by being redrawn at each position.

import micropython


@micropython.viper
def compose(dst, a, b, p):
    """Rows p[1]..p[2]-1 of dst: page a moved p[0] pixels (an even number,
    minus to the left), and page b beside it filling the gap - on the right
    when a moves left, on the left when it moves right. p[3] is the width."""
    P = ptr32(p)
    s = int(P[0]) >> 1                  # in 32-bit words: two pixels each
    y = int(P[1])
    y1 = int(P[2])
    wq = int(P[3]) >> 1
    d = ptr32(dst)
    pa = ptr32(a)
    pb = ptr32(b)
    while y < y1:
        base = y * wq
        i = 0
        if s >= 0:
            while i < s:
                d[base + i] = pb[base + i - s + wq]
                i += 1
            while i < wq:
                d[base + i] = pa[base + i - s]
                i += 1
        else:
            e = wq + s
            while i < e:
                d[base + i] = pa[base + i - s]
                i += 1
            while i < wq:
                d[base + i] = pb[base + i - s - wq]
                i += 1
        y += 1
