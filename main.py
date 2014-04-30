from datetime import datetime
import random
import yaml

from google.appengine.api import urlfetch
from google.appengine.ext import db
from google.appengine.ext import ndb
from flask import Flask, render_template, request

import feedparser

# Define the application.
app = Flask(__name__)

################################################################################
#                                             _      _                         #
#                         _ __ ___   ___   __| | ___| |                        #
#                        | '_ ` _ \ / _ \ / _` |/ _ \ |                        #
#                        | | | | | | (_) | (_| |  __/ |                        #
#                        |_| |_| |_|\___/ \__,_|\___|_|                        #
#                                                                              #
################################################################################

# All contributors appear in authors.yaml. This means each addition 
# will require an app restart. Still, it's such an infrequent operation
# and I'd rather a pre-loaded dict that I can pass around that doesn't
# need periodic refreshing.
AUTHORS = yaml.load(open('authors.yaml'))

# This is the only model used. 
class Story(ndb.Model):
    author_id = ndb.StringProperty(indexed=True, required=True)
    author = ndb.StringProperty(indexed=False, required=True)
    date = ndb.DateTimeProperty(indexed=True, required=True)
    title = ndb.StringProperty(indexed=False, required=True)
    summary = ndb.StringProperty(indexed=False, required=True)
    link = ndb.StringProperty(indexed=False, required=True)

################################################################################
#                                _             _ _                             #
#                 ___ ___  _ __ | |_ _ __ ___ | | | ___ _ __                   #
#                / __/ _ \| '_ \| __| '__/ _ \| | |/ _ \ '__|                  #
#               | (_| (_) | | | | |_| | | (_) | | |  __/ |                     #
#                \___\___/|_| |_|\__|_|  \___/|_|_|\___|_|                     #
#                                                                              #
################################################################################

@app.route('/')
@app.route('/stories/by/<author_id>')
def index(author_id=None):
    # Build the query.
    author_name = None
    if author_id:
        if author_id not in AUTHORS:
            return render_template('404.html'), 404
        else:
            q = Story.query(Story.author_id == author_id)
            author_name = AUTHORS[author_id]['name']
    else:
        q = Story.query()
    q = q.order(-Story.date)

    # Build the cursor.
    page = request.args.get('page', None)
    cursor = ndb.Cursor(urlsafe=page) if page else ndb.Cursor()
    posts, next_cursor, more = q.fetch_page(3, start_cursor=cursor)

    # XXX: TODO: Implement backwards cursors for "prev" buttons.
    prev_cursor=None

    return render_template('index.html',
                           posts=posts,
                           next_cursor=next_cursor,
                           prev_cursor=None,#next_cursor.reversed(),
                           more=more,
                           author_name=author_name,
                           author_id=author_id)


# This function is lazily implemented. It should put() only on an observable
# update, saving NDB operations. But, this app will use so few of them anyway. 
# For now, I'll embrace lazy, cognitive miser that I am.
def add_or_update_stories(author_id, blog_url):
    stories = fetch_stories(blog_url)
    for story in stories:
        try:
            blog_post = Story(id=story['link'])
            blog_post.populate(**story)
            blog_post.author_id = author_id
            blog_post.author = AUTHORS[author_id]['name']
            blog_post.put()
        except db.Error:
            pass # XXX: TODO: ADD LOGGING

    return str(stories)

@app.route('/pull_posts')
def pull_posts():
    # There is a 10 minute timeout on cron jobs. If someone's page is 
    # exceptionally bad and hangs, it could block everyone else's update. 
    # Therefore, I shuffle to probabilistically overcome error.
    authors = list(AUTHORS.keys())
    random.shuffle(authors)

    for author_id in authors:
        add_or_update_stories(author_id, AUTHORS[author_id]['feed_url'])

    return 'ok'


@app.errorhandler(404)
def page_not_found(e):
    """Return a custom 404 error."""
    return render_template('404.html'), 404


@app.errorhandler(500)
def page_not_found(e):
    """Return a custom 500 error."""
    return 'Sorry, unexpected error: {}'.format(e), 500

################################################################################
#                      _          _                                            #
#                     | |__   ___| |_ __   ___ _ __ ___                        #
#                     | '_ \ / _ \ | '_ \ / _ \ '__/ __|                       #
#                     | | | |  __/ | |_) |  __/ |  \__ \                       #
#                     |_| |_|\___|_| .__/ \___|_|  |___/                       #
#                                  |_|                                         #
################################################################################

# I like pretty dates (e.g. 2 months ago instead of Apr 10th, 2014).
# 
# The underlying function was copy/pasted from:
# http://stackoverflow.com/questions/1551382/user-friendly-time-format-in-python
#
# I turned it into a Flask Jinja2 filter.
@app.template_filter('pretty_date')
def pretty_date(time=False):
    """
    Get a datetime object or a int() Epoch timestamp and return a
    pretty string like 'an hour ago', 'Yesterday', '3 months ago',
    'just now', etc.
    """
    from datetime import datetime
    now = datetime.now()
    if type(time) is int:
        diff = now - datetime.fromtimestamp(time)
    elif isinstance(time,datetime):
        diff = now - time
    elif not time:
        diff = now - now
    second_diff = diff.seconds
    day_diff = diff.days

    if day_diff < 0:
        return ''

    if day_diff == 0:
        if second_diff < 10:
            return "just now"
        if second_diff < 60:
            return str(second_diff) + " seconds ago"
        if second_diff < 120:
            return  "a minute ago"
        if second_diff < 3600:
            return str( second_diff / 60 ) + " minutes ago"
        if second_diff < 7200:
            return "an hour ago"
        if second_diff < 86400:
            return str( second_diff / 3600 ) + " hours ago"
    if day_diff == 1:
        return "Yesterday"
    if day_diff < 7:
        return str(day_diff) + " days ago"
    if day_diff < 31:
        return str(day_diff/7) + " weeks ago"
    if day_diff < 365:
        return str(day_diff/30) + " months ago"
    return str(day_diff/365) + " years ago"



REQ_FIELDS = ['title', 'summary', 'link']
DATE_PREF_ORDERING = ['published_parsed', 'created_parsed', 'updated_parsed']
def fetch_stories(feed_url):
    # Note: feedparser is fault-tolerant. It catches all exceptions for you,
    # so you don't need a try...except block.
    feed = feedparser.parse(feed_url)

    stories = []
    for entry in feed.entries:
        # Grab the publication date, or something reasonable.
        # Note, badly implemented RSS/ATOM generators could screw this up by
        # using the incorrect date.
        date = None
        for date_field in DATE_PREF_ORDERING:
            if date_field in entry:
                date = entry[date_field]
        if date is None:
            continue
        else:
            date = datetime(*date[:-2])

        # Add the common fields, making sure each one exists.
        story = {k: entry[k] for k in REQ_FIELDS if k in entry}
        if len(story) != len(REQ_FIELDS):
            continue
        else:
            story['date'] = date
            stories.append(story)

    return stories
