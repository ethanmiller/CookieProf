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
from datetime import datetime
from cookielib import CookieJar
from twisted.internet import reactor
from twisted.web.client import Agent, CookieAgent

# dict keys as their own documentation
KY_NS_TOTAL = 'no session: total responses, just a count'
KY_NS_PERCOOK = 'no session: timestamp for unique cookie value responses'
KY_NS_LAGS = 'no session: time till response, includes timestamp'
KY_S_LAGS = 'session: time till response, includes timestamp'
KY_S_SETCOOKIE = 'session: timestamped of server response w/ setcookie'


class PollWindow(object):
    def __init__(self, site, win):
        self.cookiejar = CookieJar()
        self.agent = CookieAgent(Agent(reactor), self.cookiejar)
        self.site = site
        self.stat = StatTracker()
        self.win = win
        self.sched_call()

    def sched_call(self):
        self.r = self.agent.request('GET', self.site)
        self.r.addCallback(
            self.cbResponse,
            cjar=self.cookiejar,
            calldt=datetime.now()
        )
        self.r.addErrback(self.cbResponse)
        self.update_view()

    def update_view(self):
        self.win.addstr(0, 0, self.site)
        self.win.addstr(1, 0, str(self.stat))
        self.win.refresh()

    def cbError(self, response):
        pass

    def cbResponse(self, response, **kwargs):
        if 'cjar' not in kwargs.keys():
            return
        # update stats
        self.stat.hit(kwargs['cjar'], kwargs['calldt'])
        # if we're "sessioned" reset cookie jar here
        # self.cookiejar = cjar
        self.sched_call() # continuously call

class StatTracker():
    def __init__(self, interesting_key=None):
        self.ikey = interesting_key
        self.responses = 0
        self.gaps = []
        self.avg_gap = 0.0
        self.long_gap_dt = None
        self.longest_gap = 0.0

    def hit(self, cook, reqdt):
        ''' A request came in, collect relevant stats '''
        self.responses += 1
        # First timing related stats
        respdt = datetime.now()
        gap_delt = respdt - reqdt
        gap = gap_delt.seconds * 1.0
        if gap > self.longest_gap:
            self.long_gap_dt = reqdt
            self.longest_gap = gap
        self.gaps.append(gap)
        self.avg_gap = sum(self.gaps) / len(self.gaps)
        # Cookie stats
        # ...

    def __str__(self):
        return '\n'.join((
            'Total hits: %s' % self.responses,
            'Average response time: %s' % round(self.avg_gap, 2),
            'Slowest response time: %s' % round(self.longest_gap, 2),
            'Slowest response start: %s' % self.long_gap_dt
        ))


if __name__=='__main__':
    urls = ['https://www.pgevendorrebates.com',
            'https://www.bceincentives.com',
            'https://www.csithermal.com']
    # options:
    # -c Cookie key of interest 'which key to track unique values for'
    # -f Output file 'file to log stats after session'
    # -s session 'will save set-cookie contents, and send back for tracking a
    #            'session. Will then track new set-cookie responses'
    # -h session-hook 'which key:value to key off of to start session tracking'

    whole = curses.initscr()
    rows, cols = whole.getmaxyx()
    try:
        curses.curs_set(0)     # no annoying mouse cursor
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
            log.write('~~ %s ~~\n%s\n\n' % (p.site, p.stat))
        reactor.stop()
        curses.endwin()
    signal.signal(signal.SIGINT, fin_callback)
    reactor.run()
