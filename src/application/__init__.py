"""
Initialize Flask app

"""
from flask import Flask
from flask.ext.sqlalchemy import SQLAlchemy

import locale

app = Flask('application')
app.config.from_object('application.settings')
db = SQLAlchemy(app)

locale.setlocale(locale.LC_ALL, app.config['LOCALE'])

# Enable jinja2 loop controls extension
app.jinja_env.add_extension('jinja2.ext.loopcontrols')


# jinja custom filter for pretty cash-amounts
def jinja_cash(amount):
    if not amount:
        return 'gratis'
    big, small = ('%.2f' % amount).split('.')
    if small == '00':
        small = '-'
    return '%s,%s' % (big, small)
app.jinja_env.filters['cash'] = jinja_cash


def jinja_date(dt_obj):
    if dt_obj is None or dt_obj == []:
        return ''
    return dt_obj.strftime(u'%d. %b %Y').decode('utf-8')
app.jinja_env.filters['date'] = jinja_date


def jinja_option(attr, option=''):
    ret = 'value="' + option + '"'
    if attr == option:
        ret += ' selected="selected"'
    ret = app.jinja_env.filters['safe'](ret)
    return ret
app.jinja_env.filters['option'] = jinja_option


# A Helper function
def make_url_safe(string):
    from unidecode import unidecode
    import re

    string = unidecode(string)
    string = re.sub(' ', '_', string)
    string = re.sub('[^a-zA-Z0-9+_-]', '', string)

    return string.lower()


# Pull in URL dispatch routes and their views
import views
