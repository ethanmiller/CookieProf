#!/usr/bin/env python
"""
Per ops #249 : Script to test the behavior of the rackspace Load Balancer. In
particular we will run a DR test and take down one of the web servers to check
the behavior of the LB.

Reporting goals:
  - For session-less requests:
    - % of directions to each web server
    - average lag duration until response
    - longest lag duration, and it's timestamp (for matching against server
      outage)
    - longest *omission* of a given server (duration, start/end time)
  - For session requests:
    - average lag duration
    - longest lag duration, and it's timestamp (for matching against server
      outage)
    - occurrences (timestamp) of server set-cookie response that changes web
      server

      http://twistedmatrix.com/documents/current/web/howto/client.html
"""
import curses, signal
from twisted.internet import reactor
from twisted.web.client import Agent
from twisted.web.http_headers import Headers

class PollWindow(object):
    def __init__(self, site, win):
        self.agent = Agent(reactor)
        self.site = site
        self.dat = 0
        self.win = win
        self.sched_call()

    def sched_call(self):
        self.d = self.agent.request(
            'GET', self.site,
            Headers({'User-Agent': ['Twisted Web Client Example']}),
            None)
        self.d.addCallback(self.cbResponse)
        self.d.addErrback(self.cbResponse)
        self.update_view()

    def update_view(self):
        self.win.addstr(0, 0, self.site)
        self.win.addstr(1, 0, str(self.dat))
        self.win.refresh()

    def cbError(self, response):
        pass

    def cbResponse(self, response):
        # update stats
        self.dat += 1
        self.sched_call() # continuously call

if __name__=='__main__':
    urls = ['https://www.pgevendorrebates.com',
            'https://www.bceincentives.com',
            'https://www.csithermal.com']

    whole = curses.initscr()
    rows, cols = whole.getmaxyx()
    try:
        curses.curs_set(0)     # no annoying mouse cursor
        #curses.noecho()
        #curses.cbreak()
    except curses.error:
        pass # meh
    col_width = cols / len(urls)
    col_avail = cols
    col_offs = 0
    polls = []

    for u in urls:
        width = min(col_width, col_avail)
        col_avail -= width
        win = curses.newwin(rows, width, 0, col_offs)
        win.addstr(0, 0, u)
        polls.append(PollWindow(u, win))
        col_offs += width

    def fin_callback(signum, stackframe):
        log = open('log.txt', 'w')
        for p in polls:
            log.write('%s : %s\n' % (p.site, p.dat))
        reactor.stop()
        curses.endwin()
    signal.signal(signal.SIGINT, fin_callback)
    reactor.run()
