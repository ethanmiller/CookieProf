= CookieProf =

[ncurses](http://docs.python.org/library/curses.html) + [Twisted](http://twistedmatrix.com/trac/) utility for monitoring, and reporting on cookie behaviors for one or several URLs. While running it will hit the given URL(s) in a loop and log the response time and cookie stats. Hit Ctrl-c to exit, and the script will save it's stats to a log file (results.log by default).

The design goal here was to have a legible console display to run while you futz with load-balancers or other components that might affect cookie behavior. When finished you have a log of the results.

== Limitations ==

Currently the script expects that you have a single *cookie of interest* given as the first argument to the script. Reporting on too many cookies would just make the ncurses display impractical. Another limitation with the current design is that if too many values are returned for the given cookie key, it will overrun the available ncurses window.

== Usage ==

    Usage: cookieprof.py [options] COOKIE URL [URL ...]
    
    Options:
      -h, --help            show this help message and exit
      -f LOG_FILE, --log-file=LOG_FILE
                            File to log stats after session
      -s, --session         Will save set-cookie contents, and send back for
                            tracking a session. Will then track new set-cookie
                            responses
      -k SESS_HOOK, --session-hook=SESS_HOOK
                            Which key:value to key off of to start session
                            tracking

