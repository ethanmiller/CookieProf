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
from urlparse import urlparse
from optparse import OptionParser
from datetime import datetime
from cookielib import CookieJar
from twisted.internet import reactor
from twisted.web.client import Agent, CookieAgent

TIMEOUT = 10 # seconds

class Requestor(object):
    def __init__(self, sess, site, hook):
        self.session = sess
        self.site = site
        self.hook = ('', '')
        self.hook_ok = True
        if self.session:
            self.sess_cook, self.sess_agent = self.get_fresh()
            if hook is not None:
                self.hook = hook.split(':')
                # flag to figure out if we need to catch a particular cookie
                self.hook_ok = False

    def get_fresh(self):
        cook = CookieJar()
        agnt = CookieAgent(Agent(reactor), cook)
        return cook, agnt

    def get_stale(self):
        if self.session and not self.hook_ok:
            for c in self.sess_cook:
                if [c.name, c.value] == self.hook:
                    # Satisfied hook condition, keep this as session
                    self.hook_ok = True
                    break
            else:
                # NOTE: this is an else for the for loop
                # Haven't found hook, reset cookie jar + agent
                self.sess_cook, self.sess_agent = self.get_fresh()
        return self.sess_cook, self.sess_agent

    def request(self, call_back, err_back, dt):
        if self.session:
            cjar, agent = self.get_stale()
        else:
            cjar, agent = self.get_fresh()
        self.r = agent.request('GET', self.site)
        self.r.addCallback(
            call_back,
            cjar=cjar,
            calldt=dt
        )
        self.r.addErrback(err_back)

class PollWindow(object):
    def __init__(self, site, win, cook, opts):
        self.session = opts.session
        self.site = site
        self.stats = StatTracker(interesting_key=cook)
        self.win = win
        self.q = Requestor(False, site, opts.sess_hook)
        if self.session:
            self.sq = Requestor(True, site, opts.sess_hook)
        self.update_view(False) # once as a placeholder
        self.sched_call()
        if self.session:
            self.sess_sched_call()

    def sched_call(self):
        ''' session-less agent, recreated the cookie jar each time it's
        scheduled'''
        now = datetime.now()
        self.last_call = now
        self.q.request(self.cbResponse, self.cbError, now)

    def sess_sched_call(self):
        ''' session-ed agent, reuse the existing cookie-jar '''
        now = datetime.now()
        self.slast_call = now
        self.sq.request(self.cbSessResponse, self.cbError, now)

    def cbError(self, response):
        pass

    def timeout(self):
        self._timeout(self.last_call, self.q)
        self._timeout(self.slast_call, self.sq)

    def _timeout(self, last, rq):
        now = datetime.now()
        delt = now - last
        if delt.seconds > TIMEOUT:
            rq.r.cancel()
            # log the hit (or miss rather)
            self.stats.hit(last)
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
        is_redir = response.code in range(300, 308)
        if sess and not self.sq.hook_ok:
            # Wont count this, as the session is waiting for hook
            return
        self.stats.hit(
            kwargs['calldt'],
            cook=kwargs['cjar'],
            sess=sess,
            headr=headers,
            redir=is_redir)
        self.update_view(is_redir)

    def update_view(self, redir):
        if redir:
            # clear window, one line warning follows
            self.win.clear()
        self.win.addstr(0, 0, self.site)
        self.win.addstr(1, 0, str(self.stats))
        self.win.refresh()

class StatTracker():
    def __init__(self, interesting_key=None):
        self.cstats = CookieTracker(interesting_key)
        self.responses = 0
        self.gaps = []
        self.avg_gap = 0.0
        self.long_gap_dt = None
        self.longest_gap = 0.0
        self.redir_to = None
        self.full_print = False

    def hit(self, reqdt, cook=None, sess=False, headr=None, redir=False):
        ''' A request came in, collect relevant stats '''
        self.responses += 1
        if redir:
            self.redir_to = headr['Location']
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
        if not redir:
            self.cstats.hit(cook, headr, sess)

    def __str__(self):
        if self.redir_to is not None:
            return '!! Redirection to %s' % self.redir_to
        if self.long_gap_dt is None:
            lgap = '-'
        else:
            lgap = self.long_gap_dt.strftime('%m/%d %H:%M:%S')
        self.cstats.full_print = self.full_print
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
        self.full_print = False

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
            if kkey != self.ikey:
                continue
            ret.append('Cookie: %s' % kkey)
            tot_hits = sum([len(x) for x in vkey.values()])
            items = vkey.items()
            if not self.full_print and len(items) > 3:
                ret.append('(%s more to review in log)' % (len(items) - 3))
                items = items[-3:]
            for kval, vval in items:
                val_hits = len(vval)
                last_seen = vval[-1].strftime('%m/%d %H:%M:%S')
                perc = 100*((val_hits*1.0)/tot_hits)
                ret.extend([
                    ' - %s' % kval,
                    '  - %s hits (%s%%)' % (val_hits, round(perc, 1)),
                    '  - last seen %s' % last_seen
                ])
        return ret

def valid_url(u):
    '''We're just going to expect a scheme and a netloc, and call that a good
    URL '''
    r = urlparse(u)
    return '' not in [r.scheme, r.netloc]

if __name__=='__main__':
    usage = "usage: %prog [options] COOKIE URL [URL ...]"
    parser = OptionParser(usage=usage)
    parser.add_option(
        '-f',
        '--log-file',
        type='string',
        dest='log_file',
        default='results.log',
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
    opts, args = parser.parse_args()

    if len(args) < 2:
        parser.error('Must give us one COOKIE key and at least one URL')

    cook, urls = args[:1][0], args[1:]
    if valid_url(cook):
        parser.error('Your COOKIE argument looks a lot like a URL, I\'m '
                     'guessing you just forgot to add the COOKIE')

    for u in urls:
        if not valid_url(u):
            parser.error('%s doesn\'t look like a valid URL. Please include '
                         'the scheme (eg. http://)' % u)

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
        polls.append(PollWindow(u, win, cook, opts))
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
            p.stats.full_print = True
            log.write('~~ %s ~~\n%s\n\n' % (p.site, p.stats))
        reactor.stop()
        curses.endwin()
    signal.signal(signal.SIGINT, fin_callback)
    reactor.run()
