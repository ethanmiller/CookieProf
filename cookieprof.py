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
from optparse import OptionParser
from datetime import datetime
from cookielib import CookieJar
from twisted.internet import reactor
from twisted.web.client import Agent, CookieAgent

TIMEOUT = 10 # seconds

class PollWindow(object):
    def __init__(self, site, win, opts):
        self.session = opts.session
        self.site = site
        self.stats = StatTracker(interesting_key=opts.interesting_cookie)
        self.win = win
        if self.session:
            if opts.sess_hook is not None:
                self.hook = opts.sess_hook.split(':')
                # flag to figure out if we need to catch a particular cookie
                self.hook_ok = False
            else:
                self.hook = ('', '')
                self.hook_ok = True
            self.sess_cook = CookieJar()
            self.sess_agent = CookieAgent(Agent(reactor), self.sess_cook)
        self.update_view() # once as a placeholder
        self.sched_call()
        if self.session:
            self.sess_sched_call()

    def sched_call(self):
        ''' session-less agent, recreated the cookie jar each time it's
        scheduled'''
        now = datetime.now()
        self.last_call = now
        cjar = CookieJar()
        agent = CookieAgent(Agent(reactor), cjar)
        self.r = agent.request('GET', self.site)
        self.r.addCallback(
            self.cbResponse,
            cjar=cjar,
            calldt=now
        )
        self.r.addErrback(self.cbResponse)

    def sess_sched_call(self):
        ''' session-ed agent, reuse the existing cookie-jar '''
        now = datetime.now()
        self.last_call = now
        cjar, agent = self.get_sess_cookie()
        self.sr = agent.request('GET', self.site)
        self.sr.addCallback(
            self.cbSessResponse,
            cjar=cjar,
            calldt=now
        )
        self.r.addErrback(self.cbResponse)

    def get_sess_cookie(self):
        ''' Returns session cookie and agent, checks to see if sess_hook
        criteria is met '''
        if not self.hook_ok:
            # examine existing cookie - might meet criteria
            pass
        return self.sess_cook, self.sess_agent

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
        if 'cjar' in kwargs.keys():
            self.hit(response, False, **kwargs)
            self.sched_call() # continuously call

    def cbSessResponse(self, response, **kwargs):
        if 'cjar' in kwargs.keys():
            self.hit(response, True, **kwargs)
            self.sess_sched_call() # continuously call

    def hit(self, response, sess, **kwargs):
        headers = dict(response.headers.getAllRawHeaders())
        self.stats.hit(
            kwargs['calldt'],
            cook=kwargs['cjar'],
            sess=sess,
            headr=headers,
            redir=response.code==301)
        self.update_view()


class StatTracker():
    def __init__(self, interesting_key=None):
        self.cstats = CookieTracker(interesting_key)
        self.responses = 0
        self.gaps = []
        self.avg_gap = 0.0
        self.long_gap_dt = None
        self.longest_gap = 0.0

    def hit(self, reqdt, cook=None, sess=False, headr=None, redir=False):
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
        self.cstats.hit(cook, headr, sess)

    def __str__(self):
        if self.long_gap_dt is None:
            lgap = '-'
        else:
            lgap = self.long_gap_dt.strftime('%m/%d %H:%M:%S')
        return '\n'.join((
            'Total hits: %s' % self.responses,
            'Average response time: %s' % round(self.avg_gap, 2),
            'Slowest response time: %s' % round(self.longest_gap, 2),
            'Slowest response start: %s' % lgap,
            '',
            str(self.cstats)
        ))

class CookieTracker():
    def __init__(self, interesting_key):
        self.ikey = interesting_key
        # track sessioned keys identically, but separately
        self.no_sess = {}
        self.sess = {}
        self.set_cookies = {}

    def hit(self, cook, headr=None, sess=False):
        if cook is None:
            return
        d = self.no_sess
        if sess:
            d = self.sess
        for c in cook:
            stats = d.setdefault(c.name, {})
            hits = stats.setdefault(c.value, [])
            hits.append(datetime.now())

    def __str__(self):
        ret = ['---- No Session ----']
        ret.extend(self.report(self.no_sess))
        if self.sess:
            ret.append('---- Session ----')
            ret.extend(self.report(self.sess))
        return '\n'.join(ret)

    def report(self, dat):
        ret = []
        for kkey, vkey in dat.iteritems():
            if self.ikey and kkey != self.ikey:
                continue
            ret.append('Cookie: %s' % kkey)
            tot_hits = sum([len(x) for x in vkey.values()])
            for kval, vval in vkey.iteritems():
                val_hits = len(vval)
                last_seen = vval[-1].strftime('%m/%d %H:%M:%S')
                perc = 100*((val_hits*1.0)/tot_hits)
                ret.extend([
                    ' - %s' % kval,
                    '  - %s hits (%s%%)' % (val_hits, round(perc, 1)),
                    '  - last seen %s' % last_seen
                ])
        return ret


if __name__=='__main__':
    usage = "usage: %prog [options] URL [URL ...]"
    parser = OptionParser(usage=usage)
    parser.add_option(
        '-c',
        '--cookie',
        type='string',
        dest='interesting_cookie',
        help='Which cookie key to track unique values for'
    )
    parser.add_option(
        '-f',
        '--log-file',
        type='string',
        dest='log_file',
        default='cookieprof.log',
        help='File to log stats after session'
    )
    parser.add_option(
        '-s',
        '--session',
        dest='session',
        action='store_true',
        help=('Will save set-cookie contents, and send back for tracking a '
              'session. Will then track new set-cookie responses')
    )
    parser.add_option(
        '-k',
        '--session-hook',
        type='string',
        dest='sess_hook',
        help='Which key:value to key off of to start session tracking'
    )
    opts, urls = parser.parse_args()
    if len(urls) == 0:
        parser.error('Must give us at least one URL')

    whole = curses.initscr()
    rows, cols = whole.getmaxyx()
    try:
        curses.curs_set(0)     # no annoying mouse cursor
    except curses.error:
        pass # encountered this in osx default terminal
    col_width = cols / len(urls)
    col_avail = cols
    col_offs = 0
    polls = []

    for u in urls:
        width = min(col_width, col_avail)
        col_avail -= width
        win = curses.newwin(rows, width, 0, col_offs)
        win.addstr(0, 0, u)
        polls.append(PollWindow(u, win, opts))
        col_offs += width

    # Admittedly goofy timeout handling :(
    def run_timeouts():
        for p in polls:
            p.timeout()
        reactor.callLater(TIMEOUT, run_timeouts)
    reactor.callLater(TIMEOUT, run_timeouts)

    def fin_callback(signum, stackframe):
        log = open(opts.log_file, 'w')
        for p in polls:
            log.write('~~ %s ~~\n%s\n\n' % (p.site, p.stats))
        reactor.stop()
        curses.endwin()
    signal.signal(signal.SIGINT, fin_callback)
    reactor.run()
