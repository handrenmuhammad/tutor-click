-- Tutor Click registers these hotkeys by itself while it runs and removes them on quit,
-- so you normally do NOT need this file.
-- Use it only if you start the app with TUTOR_CLICK_BINDS=0 and want the keys in your own config.
local tutor = "/home/handren/.local/bin/tutor-click"
hl.bind("SUPER + ALT + space", hl.dsp.exec_cmd(tutor .. " ptt-down"))                     -- hold to talk
hl.bind("SUPER + ALT + space", hl.dsp.exec_cmd(tutor .. " ptt-up"), { release = true })  -- release to send
hl.bind("SUPER + ALT + N", hl.dsp.exec_cmd(tutor .. " next"))                            -- next step / continue
hl.bind("SUPER + ALT + T", hl.dsp.exec_cmd(tutor .. " type"))                            -- type a question
hl.bind("SUPER + ALT + M", hl.dsp.exec_cmd(tutor .. " mode toggle"))                     -- who clicks: you / tutor
hl.bind("SUPER + ALT + E", hl.dsp.exec_cmd(tutor .. " explain"))                         -- stop and explain more
hl.bind("SUPER + ALT + Escape", hl.dsp.exec_cmd(tutor .. " stop"))                       -- stop talking and acting
