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

TIMEOUT = 10 # seconds
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
        self.stats = StatTracker()
        self.win = win
        self.sched_call()
        self.last_call = datetime.now()

    def sched_call(self):
        now = datetime.now()
        self.last_call = now
        self.r = self.agent.request('GET', self.site)
        self.r.addCallback(
            self.cbResponse,
            cjar=self.cookiejar,
            calldt=now
        )
        self.r.addErrback(self.cbResponse)
        self.update_view()

    def update_view(self):
        self.win.addstr(0, 0, self.site)
        self.win.addstr(1, 0, str(self.stats))
        self.win.refresh()

    def cbError(self, response):
        pass

    def timeout(self):
        now = datetime.now()
        delt = now - self.last_call
        if delt.seconds > TIMEOUT:
            self.r.cancel()
            # log the hit (or miss rather)
            self.stats.hit(self.last_call)
            # and try again
            self.sched_call()

    def cbResponse(self, response, **kwargs):
        if 'cjar' not in kwargs.keys():
            return
        # update stats
        self.stats.hit(kwargs['calldt'], kwargs['cjar'])
        # if we're "sessioned" reset cookie jar here
        # self.cookiejar = cjar
        self.sched_call() # continuously call

class StatTracker():
    def __init__(self, interesting_key=None):
        self.cstats = CookieTracker(interesting_key)
        self.responses = 0
        self.gaps = []
        self.avg_gap = 0.0
        self.long_gap_dt = None
        self.longest_gap = 0.0

    def hit(self, reqdt, cook=None):
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
        self.cstats.hit(cook)

    def __str__(self):
        return '\n'.join((
            'Total hits: %s' % self.responses,
            'Average response time: %s' % round(self.avg_gap, 2),
            'Slowest response time: %s' % round(self.longest_gap, 2),
            'Slowest response start: %s' % self.long_gap_dt,
            '',
            str(self.cstats)
        ))

class CookieTracker(dict):
    def __init__(self, interesting_key):
        self.ikey = interesting_key

    def hit(self, cook):
        if cook is None:
            return
        for c in cook:
            stats = self.setdefault(c.name, {})
            hits = stats.setdefault(c.value, [])
            hits.append(datetime.now())

    def __str__(self):
        ret = []
        for kkey, vkey in self.iteritems():
            ret.append('Cookie: %s' % kkey)
            tot_hits = sum([len(x) for x in vkey.values()])
            for kval, vval in vkey.iteritems():
                val_hits = len(vval)
                last_seen = vval[-1]
                ret.extend([
                    ' - %s' % kval,
                    '  - %s hits (%s%%)' % (val_hits, 100*(val_hits/tot_hits)),
                    '  - last seen at %s' % last_seen
                ])
        return '\n'.join(ret)


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

    # Admittedly goofy timeout handling :(
    def run_timeouts():
        for p in polls:
            p.timeout()
        reactor.callLater(10, run_timeouts)
    reactor.callLater(10, run_timeouts)

    def fin_callback(signum, stackframe):
        log = open('log.txt', 'w')
        for p in polls:
            log.write('~~ %s ~~\n%s\n\n' % (p.site, p.stats))
        reactor.stop()
        curses.endwin()
    signal.signal(signal.SIGINT, fin_callback)
    reactor.run()
