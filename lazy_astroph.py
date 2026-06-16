#!/usr/bin/env python3
 
 
import argparse
import datetime as dt
import json
import os
import platform
import smtplib
import sys
from collections import defaultdict
from email.mime.text import MIMEText
 
import feedparser
import requests
 
# python 2 and 3 do different things with urllib
try:
    from urllib.request import urlopen
except ImportError:
    from urllib2 import urlopen
 
 
class Paper:
    """a Paper is a single paper listed on arXiv.  In addition to the
       paper's title, ID, and URL (obtained from arXiv), we also store
       which keywords it matched and which Slack channel it should go
       to"""
 
    def __init__(self, arxiv_id, title, url, keywords_by_channel, lead_author=None):
        self.arxiv_id = arxiv_id
        self.title = title.replace("'", r"")
        self.url = url
        self.lead_author = lead_author
        self.keywords_by_channel = keywords_by_channel
        self.keywords = []
        for kws in keywords_by_channel.values():
            self.keywords.extend(kws)
        self.posted_to_slack = 0
 
    def __str__(self):
        t = " ".join(self.title.split())  # remove extra spaces
        return f"{self.arxiv_id} : {t}\n  {self.url}\n"
 
    def kw_str(self):
        """ return the union of keywords """
        return ", ".join(self.keywords)
 
    def __lt__(self, other):
        """we compare Papers by the number of keywords, and then
           alphabetically by the union of their keywords"""
 
        if len(self.keywords) == len(other.keywords):
            return self.kw_str() < other.kw_str()
 
        return len(self.keywords) < len(other.keywords)
 
 
class Keyword:
    """a Keyword includes: the text we should match, how the matching
       should be done (unique or any), which words, if present, negate
       the match, and what Slack channel this keyword is associated with"""
 
    def __init__(self, name, matching="any", channel=None, excludes=None):
        self.name = name
        self.matching = matching
        self.channel = channel
        self.excludes = list(set(excludes))
 
    def __str__(self):
        return f"{self.name}: matching={self.matching}, channel={self.channel}, NOTs={self.excludes}"
 
 
class AstrophQuery:
    """ a class to define a query to the arXiv astroph papers """
 
    def __init__(self, start_date, end_date, max_papers):
        self.start_date = start_date
        self.end_date = end_date
        self.max_papers = max_papers
 
        self.base_url = "http://export.arxiv.org/api/query?"
        self.sort_query = f"max_results={self.max_papers}&sortBy=submittedDate&sortOrder=descending"
 
        self.subcat = ["GA", "CO", "EP", "HE", "IM", "SR"]
 
    def get_cat_query(self):
        """ create the category portion of the astro ph query """
 
        cat_query = "%28"  # open parenthesis
        for n, s in enumerate(self.subcat):
            cat_query += f"astro-ph.{s}"
            if n < len(self.subcat)-1:
                cat_query += "+OR+"
            else:
                cat_query += "%29"  # close parenthesis
 
        return cat_query
 
    def get_range_query(self):
        """ get the query string for the date range """
 
        # here the 2000 on each date is 8:00pm
        start = self.start_date.strftime("%Y%m%d")
        end = self.end_date.strftime("%Y%m%d")
        range_str = f"[{start}2000+TO+{end}2000]"
        range_query = f"lastUpdatedDate:{range_str}"
        return range_query
 
    def get_url(self):
        """ create the URL we will use to query arXiv """
 
        cat_query = self.get_cat_query()
        range_query = self.get_range_query()
 
        full_query = f"search_query={cat_query}+AND+{range_query}&{self.sort_query}"
 
        return self.base_url + full_query
 
    def do_query(self, keywords=None, seen_ids=None):
        """ perform the actual query """
 
        # note, in python3 this will be bytes not str
        response = urlopen(self.get_url()).read()
        response = response.replace(b"author", b"contributor")
 
        feed = feedparser.parse(response)
 
        if feed.feed.opensearch_totalresults == 0:
            sys.exit("no results found")
 
        results = []
 
        for e in feed.entries:
 
            arxiv_id = e.id.split("/abs/")[-1]
            title = e.title.replace("\n", " ")
 
            # skip papers we've already seen
            if seen_ids and arxiv_id in seen_ids:
                continue
 
            # get lead author
            lead_author = None
            if e.contributors:
                lead_author = e.contributors[0].get("name", None)
 
            # link
            for l in e.links:
                if l.rel == "alternate":
                    url = l.href
 
            abstract = e.summary
 
            # any keyword matches?
            keys_matched = defaultdict(list)
            for k in keywords:
                # first check the "NOT"s
                excluded = False
                for n in k.excludes:
                    if n in abstract.lower().replace("\n", " ") or n in title.lower():
                        excluded = True
 
                if excluded:
                    continue
 
                if k.matching == "any":
                    if k.name in abstract.lower().replace("\n", " ") or k.name in title.lower():
                        keys_matched[k.channel].append(k.name)
 
                elif k.matching == "unique":
                    qa = [l.lower().strip('\":.,!?') for l in abstract.split()]
                    qt = [l.lower().strip('\":.,!?') for l in title.split()]
                    if k.name in qa + qt:
                        keys_matched[k.channel].append(k.name)
 
            if keys_matched:
                results.append(Paper(arxiv_id, title, url, dict(keys_matched), lead_author=lead_author))
 
        return results
 
 
def report(body, subject, sender, receiver):
    """ send an email """
 
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = receiver
 
    try:
        sm = smtplib.SMTP('localhost')
        sm.sendmail(sender, receiver, msg.as_string())
    except smtplib.SMTPException:
        sys.exit("ERROR sending mail")
 
 
def search_astroph(keywords, seen_ids=None):
    """ do the actual search though astro-ph by first querying astro-ph
        for the latest papers and then looking for keyword matches"""
 
    today = dt.date.today()
    day = dt.timedelta(days=1)
 
    max_papers = 1000
 
    q = AstrophQuery(today - 10*day, today, max_papers)
    print(q.get_url())
 
    papers = q.do_query(keywords=keywords, seen_ids=seen_ids)
 
    papers.sort(reverse=True)
 
    return papers
 
 
def send_email(papers, mail=None):
 
    # compose the body of our e-mail
    body = ""
 
    # sort papers by keywords
    current_kw = None
    for p in papers:
        if not p.kw_str() == current_kw:
            current_kw = p.kw_str()
            body += f"\nkeywords: {current_kw}\n\n"
 
        body += f"{p}\n"
 
    # e-mail it
    if not len(papers) == 0:
        if not mail is None:
            report(body, "astro-ph papers of interest",
                   f"lazy-astroph@{platform.node()}", mail)
        else:
            print(body)
 
 
def slack_post(papers, channel_req, username=None, icon_emoji=None, webhook=None):
    """ post the information to a slack channel """
 
    # loop by channel
    for c in channel_req:
        channel_body = ""
        for p in papers:
            if not p.posted_to_slack:
                if c in p.keywords_by_channel:
                    if len(p.keywords_by_channel[c]) >= channel_req[c]:
                        keywds = ", ".join(p.keywords).strip()
                        channel_body += f"{p.arxiv_id} : {p.title}\n  {p.lead_author} {p.url}\n  [{keywds}]\n\n"
                        p.posted_to_slack = 1
 
        if webhook is None:
            print(f"channel: {c}")
            print(channel_body)
            continue
 
        payload = {}
        payload["channel"] = c
        if username is not None:
            payload["username"] = username
        if icon_emoji is not None:
            payload["icon_emoji"] = icon_emoji
        payload["text"] = channel_body
 
        requests.post(webhook, json=payload)
 
 
def doit():
    """ the main driver for the lazy-astroph script """
 
    # parse runtime parameters
    parser = argparse.ArgumentParser()
 
    parser.add_argument("-m", help="e-mail address to send report to",
                        type=str, default=None)
    parser.add_argument("inputs", help="inputs file containing keywords",
                        type=str, nargs=1)
    parser.add_argument("-w", help="file containing slack webhook URL",
                        type=str, default=None)
    parser.add_argument("-u", help="slack username appearing in post",
                        type=str, default=None)
    parser.add_argument("-e", help="slack icon_emoji appearing in post",
                        type=str, default=None)
    parser.add_argument("--dry_run",
                        help="don't send any mail or slack posts and don't update the marker where we left off",
                        action="store_true")
    args = parser.parse_args()
 
    # get the keywords
    keywords = []
    try:
        f = open(args.inputs[0])
    except:
        sys.exit("ERROR: unable to open inputs file")
    else:
        channel = None
        channel_req = {}
        for line in f:
            l = line.lower().rstrip()
 
            if l == "":
                continue
 
            elif l.startswith("#") or l.startswith("@"):
                # this line defines a channel
                ch = l.split()
                channel = ch[0]
                if len(ch) == 2:
                    requires = int(ch[1].split("=")[1])
                else:
                    requires = 1
                channel_req[channel] = requires
 
            else:
                # this line has a keyword (and optional NOT keywords)
                if "not:" in l:
                    kw, nots = l.split("not:")
                    kw = kw.strip()
                    excludes = [x.strip() for x in nots.split(",")]
                else:
                    kw = l.strip()
                    excludes = []
 
                if kw[len(kw)-1] == "-":
                    matching = "unique"
                    kw = kw[:len(kw)-1]
                else:
                    matching = "any"
 
                keywords.append(Keyword(kw, matching=matching,
                                        channel=channel, excludes=excludes))
 
    # load the set of already-seen paper IDs
    param_file = ".lazy_astroph"
    try:
        with open(param_file) as f:
            seen_ids = set(line.strip() for line in f if line.strip())
    except:
        seen_ids = set()
 
    papers = search_astroph(keywords, seen_ids=seen_ids)
 
    if not args.dry_run:
        send_email(papers, mail=args.m)
 
        if not args.w is None:
            try:
                f = open(args.w)
            except:
                sys.exit("ERROR: unable to open webhook file")
 
            webhook = str(f.readline())
            f.close()
        else:
            webhook = None
 
        slack_post(papers, channel_req, icon_emoji=args.e, username=args.u, webhook=webhook)
 
        # save all seen IDs (previous + new)
        all_ids = seen_ids | {p.arxiv_id for p in papers}
        try:
            with open(param_file, "w") as f:
                for pid in all_ids:
                    f.write(pid + "\n")
        except:
            sys.exit("ERROR: unable to write parameter file")
    else:
        send_email(papers, mail=None)
 
 
if __name__ == "__main__":
    doit()
