# coding=utf-8
"""
views.py

Route handlers for HTML

"""

from flask import request, render_template, flash, url_for, redirect, send_from_directory, session, g
from werkzeug import secure_filename

from application import app, make_url_safe, db
from models import Item, Transaction, Buy, Lend, CATEGORIES, PROGRESS, GROUPS

from functools import wraps

import datetime
import uuid


def calendar(item, month_offset=0):
    ''' Quick'n'dirty day-structs to build a calendar overview '''

    today = datetime.date.today()
    month = today.month-1 + month_offset
    year = today.year + month/12
    month = month % 12 +1
    day1 = datetime.date(year, month, 1)  # 1. of current month
    day1 = day1 - datetime.timedelta(7)  # Go back a week
    while day1.weekday():  # Skip to the next monday
        day1 = day1 + datetime.timedelta(1)
    
    days = []
    for dt in range(7*6):  # Generate days for six weeks
        d = day1 + datetime.timedelta(dt)
        day = {}
        in_stock = item.in_stock(d)

        day['weekday'] = d.weekday()
        day['nr'] = d.day
        # strftime apparently has unicode-issues
        day['month'] = d.strftime(u'%B %Y'.encode('utf-8')).decode('utf-8')
        day['title'] = u'%u Stück' % in_stock
        day['class'] = 'blur' if not d.month == month else ''
        day['class'] += ' today' if d == today else ''
        day['class'] += ' out' if not in_stock else ''

        days += day,
    return days


# LAZYNESS HELPERS
def request_or_none(key):
    val = request.form.get(key)
    if val or val == '0':
        return val
    return None

def session_or_empty(key):
    if key in session:
        return session[key]
    return []


@app.before_request
def create_transaction():
    ''' Creates a transaction for the user's shopping cart. Apparently, adding
    relations to transient transactions somehow automatically adds them to the
    current session. As we usually don't want to commit semi-complete
    transactions, DB modifications are prepent with session.rollback(). '''

    ta = Transaction()

    lending = session_or_empty('lend')
    buying = session_or_empty('buy')

    ta.group = session_or_empty('group')
    if ta.group == []:
        ta.group = 'int'

    for id in lending:
        item = Item.query.get(id)
        ta.lend[id] = Lend(item, lending[id])

    for id in buying:
        item = Item.query.get(id)
        ta.buy[id] = Buy(item, buying[id])

    ta.date_start = session_or_empty('date_start')
    ta.date_end = session_or_empty('date_end')
    ta.name = session_or_empty('name')
    ta.email = session_or_empty('email')
    ta.tel = session_or_empty('tel')

    g.ta = ta


@app.after_request
def dump_transaction(response):
    ta = g.ta

    session['lend'] = {id: ta.lend[id].amount for id in ta.lend}
    session['buy'] = {id: ta.buy[id].amount for id in ta.buy}

    session['date_start'] = datetime.datetime.combine(ta.date_start, datetime.time()) if ta.date_start else None
    session['date_end'] = datetime.datetime.combine(ta.date_end, datetime.time()) if ta.date_end else None

    session['name'] = ta.name
    session['email'] = ta.email
    session['tel'] = ta.tel

    session['group'] = ta.group

    return response


def pjax(template, **kwargs):
    ''' Filter which items to show and determine whether the request was made
        by PJAX.
    '''

    # Filter by category
    category = session_or_empty('category') or 'all'

    if category == 'all':
        items = Item.query.order_by('name').all()
    else:
        items = Item.query.filter_by(category=category)

    ta = g.ta

    for item in items:
        item.gone=False  # Prepare a flag for availability

    # Filter by group
    if ta.group and not 'logged_in' in session:
        items = filter(lambda i: i.buyable or i.lendable, items)

    # Filter by date
    if ta.date_start and ta.date_end:
        colliding_tas = Transaction.query.filter(db.and_(
                           Transaction.date_end >= ta.date_start,
                           Transaction.date_start <= ta.date_end
                       )).all()
        for item in items:
            for colliding_ta in colliding_tas:
                gone = 0
                if item.id in colliding_ta.lend:
                    gone += colliding_ta.lend[item.id].amount
                if gone >= item.count and item in items:
                    item.gone=True


    # Safe referrer explicitly to session as request.referer did not work
    session['referrer'] =  request.url

    kwargs['logged_in'] = 'logged_in' in session
    kwargs['items'] = items
    kwargs['ta'] = ta

    # Pjax fork
    if "X-PJAX" in request.headers:
        return render_template(template, **kwargs)
    
    return render_template('base.html',
                           template = template,
                           category=category,
                           **kwargs
                           )


def same():
    ''' Keep the user on the current view, but refresh it. '''
    return redirect( session['referrer'] )


def login_required(f):
    ''' Decorator for protected views. '''
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not 'logged_in' in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/')
def list():
    return pjax('content.html')


@app.route('/uploads/<path:filename>')
def send_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


### ITEM-FILTER VIEWS

@app.route('/filter/category/<string:category>')
def cat_filter(category):
    if not category in CATEGORIES:
        flash(u'Kategorie "%s" ungültig!' % category)
    else:
        session['category'] = category
    return same()


@app.route('/filter/group/<string:group>')
def group_filter(group):
    if not group in GROUPS:
        flash(u'Gruppe "%s" ungültig!' % group)
    else:
        g.ta.group=group
    return same()


@app.route('/filter/between/<string:start>/and/<string:end>')
def date_filter(start, end):
    ta = g.ta

    def parse_date(string):
        try:
            return datetime.datetime.strptime(string.encode('utf-8'), '%d._%b_%Y')
        except ValueError:
            flash(u'"%s" ist emfall kein gültiges Datum!' % string)
            return None

    ta.date_start = parse_date(start)
    ta.date_end = parse_date(end)

    if not ta.date_start or not ta.date_end:
        return same()

    # Enforce date order
    if ta.date_start > ta.date_end:
        ta.date_start, ta.date_end = ta.date_end, ta.date_start

    return same()


@app.route('/filter/none')
def clear_filter():
    session.pop('category', None)
    ta = g.ta
    ta.date_start = None
    ta.date_end = None
    
    return same()


### CART VIEWS

@app.route('/item/<id>/lend')
def item_lend(id):
    ta = g.ta

    if id in ta.lend:
        ta.lend[id].amount += 1

    else:
        it= Item.query.get(id)
        ta.lend[id] = Lend(it)

    #flash('%s eingepackt.'%id, 'success')
    return same()
    

@app.route('/item/<id>/buy')
def item_buy(id):
    ta = g.ta

    if id in ta.buy:
        ta.buy[id].amount += 1

    else:
        it= Item.query.get(id)
        ta.buy[id] = Buy(it)

    #flash('%s eingepackt.'%id, 'success')
    return same()


@app.route('/item/<id>/unlend')
def item_unlend(id):
    ta = g.ta

    if not id in ta.lend or ta.lend[id].amount < 1:
        flash('%s nicht eingepackt'%id)
        return item(id)

    ta.lend[id].amount -= 1

    if ta.lend[id].amount == 0:
        ta.lend.pop(id)
    return same()


@app.route('/item/<id>/unbuy')
def item_unbuy(id):
    ta = g.ta

    if not id in ta.buy or ta.buy[id].amount < 1:
        flash('%s nicht eingepackt'%id)
        return item(id)

    ta.buy[id].amount -= 1

    if ta.buy[id].amount == 0:
        ta.buy.pop(id)
    return same()
    

@app.route('/cart/empty')
def cart_empty():
    g.ta.lend.clear()
    g.ta.buy.clear()

    return list()


@app.route('/cart/checkout')
def cart_checkout():

    # TODO read and set a cookie for 
    # name, address, email, phone, group

    ta = g.ta

    if ta.lend and not ta.date_start and not ta.date_end:
        flash(u'Gib einen Zeitraum für deine Bestellung an!', '#sidebar .datepicker:first-child')
    else:
        for li in ta.lend.values():
            if not li.item.lendable:
                flash(u'Für die gewählte Nutzergruppe leider nicht verfügbar. <a href="%s"><i class="icon-undo"></i> zurücklegen</a>?' % url_for('item_unlend', id=li.item.id), '#lend_'+li.item.id)
            elif not li.valid():
                flash(u'Bereits verliehen! <a href="%s"><i class="icon-calendar"></i> Daten checken</a> oder <a href="%s"><i class="icon-undo"></i> zurücklegen</a>?' % (url_for('check_stock', id=li.item.id), url_for('item_unlend', id=li.item.id)), '#lend_'+li.item.id)

    return pjax('checkout.html')


@app.route('/cart/submit', methods=['POST'])
def cart_submit():
    ta = g.ta

    ta.name = request.form.get('name')
    ta.email = request.form.get('email')
    ta.tel = request.form.get('tel')
    ta.payment = request.form.get('payment')
    ta.delivery = request.form.get('delivery')
    ta.remarks = request.form.get('remarks')


    # Validate items
    ok = True
    for li in ta.lend.values():
        if not li.valid():
            flash(u'Für die gewählte Verleihdauer nicht an Lager <a href="%s"><i class="icon-calendar"></i></a>' % url_for('check_stock', id=li.item.id), '#lend_'+li.item.id)
            ok = False

    # Validate fields
    required = 'name email tel'.split(' ')
    for field in required:
        val = request.form.get(field)
        if not val:
            flash('Bitte gib deine %s an!' % field, 'input[name="%s"]' % field)
            ok = False

    if not ok:
        return same()

    db.session.add(ta)
    db.session.commit()

    clear_filter()
    cart_empty()

    return thankyou()


@app.route('/cart/thankyou')
def thankyou():
    return pjax('thankyou.html')
    

### ITEM VIEWS

@app.route('/item/<id>')
def item(id):
    item = Item.query.get_or_404(id)
    return pjax('detail.html', item=item) 


@app.route('/item/<id>/stock', methods=['GET'])
def check_stock(id):
    item = Item.query.get_or_404(id)

    months = []
    for m in range(6):
        months += calendar(item, m),

    return pjax('stock.html', item=item, months=months) 


### ITEM ADMIN VIEWS

@app.route('/item/<id>/destroy', methods=['GET', 'POST'])
@login_required
def item_destroy(id):
    item = Item.query.get_or_404(id)

    # TODO: check dependencies

    db.session.rollback() # See comment in create_transaction()
    db.session.delete(item)
    db.session.commit()

    flash(u'%s gelöscht.'%id)
    return redirect(url_for('list'))


@app.route('/item_create', methods=['GET'])
@app.route('/item/<id>/edit', methods=['GET'])
@login_required
def item_edit(id=None):
    if id:
        item = Item.query.get_or_404(id)
        related = ', '.join(i.id for i in item.related)
        if not related == '':
            related += ', '

    else:
        item = Item()
        # The following attributes are needed to show this dummy-item
        item.count = 1
        item.name = ''
        related = ''

    itemlist = Item.query.all()
    itemlist = ' '.join(i.id for i in itemlist)

    # Require form
    if request.method == 'GET':
        return pjax('create_item.html', item=item, itemlist=itemlist, related=related)


@app.route('/item_update', methods=['POST'])
@app.route('/item/<id>/update', methods=['POST'])
def item_post(id=None):
    db.session.rollback() # See comment in create_transaction()
    if id:
        itm = Item.query.get_or_404(id)
    else:
        id = make_url_safe(request.form.get('name'))
        itm = Item(id=id)
        db.session.add(itm)
        
    itm.name = request.form.get('name')
    itm.description = request.form.get('description')
    itm.count = int(request.form.get('count')) if request.form.get('count') else 1

    itm.tax_base_int = request_or_none('tax_base_int')
    itm.tax_base_edu = request_or_none('tax_base_edu')
    itm.tax_base_ext = request_or_none('tax_base_ext')
    itm.tax_int = request_or_none('tax_int')
    itm.tax_edu = request_or_none('tax_edu')
    itm.tax_ext = request_or_none('tax_ext')


    itm.related = []
    for iid in request.form.get('related').split(', '):
        if iid == '':
            continue
        i = Item.query.get(iid)
        if i is None:
            flash(u'Artikel "%s" ist nicht bekannt!' % ii )
            continue
        itm.related.append(i)

    itm.tax_period = request.form.get('tax_period')

    itm.price_buy = request_or_none('price_buy')

    itm.category = request.form.get('category')

    db.session.commit()

    # Update image if necessary
    file = request.files['image']
    if file:
        import os
        from PIL import Image as i
        filename = secure_filename(id).lower() + '.jpg'

        image = i.open(file)
        if image.mode != "RGB":
            image = image.convert("RGB")

        image.save(os.path.join(app.config['UPLOAD_FOLDER'], 'full', filename), "jpeg")

        w  = image.size[0]
        h = image.size[1]

        aspect = w / float(h)
        ideal_aspect = 1.0

        if aspect > ideal_aspect:  # Then crop the left and right edges:
            w_ = int(ideal_aspect * h)
            offset = (w - w_)/2
            resize = (offset, 0, w - offset, h)
        else:  # ... crop the top and bottom:
            h_ = int(w/ideal_aspect)
            offset = (h - h_)/2
            resize = (0, offset, w, h - offset)

        image = image.crop(resize).resize((140, 140), i.ANTIALIAS)
        image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename), "jpeg")

    return redirect( url_for('item', id=id) )


### LOGIN/OUT VIEWS

@app.route('/login', methods=['GET', 'POST'])
def login():
    from Crypto.Hash import SHA256
    from Crypto.Random import get_random_bytes
    from base64 import b64encode

    if 'logged_in' in session:
        return redirect(url_for('admin'))

    if request.method == 'POST':
        challenge = session['challenge']
        hsh_given = request.form['hash']
        h = SHA256.new(challenge+app.config['PASSWORD'])
        hsh_valid = h.hexdigest()

        if hsh_valid == hsh_given:
            session['logged_in'] = True
            flash('Hallo Chef.')
            return redirect(url_for('admin'))

        flash(u'Ungültiges Kennwort!')
    
    challenge = b64encode(get_random_bytes(64))
    session['challenge'] = challenge
    return pjax('login.html', challenge=challenge)


@app.route('/logout')
@login_required
def logout():
    flash('Abgemeldet!')
    session.pop('logged_in', None)
    return redirect(url_for('list'))


### TRANSACTION ADMIN VIEWS

@app.route('/admin')
@login_required
def admin():
    transactions = Transaction.query.order_by('date desc').all()
    return pjax('admin.html', transactions=transactions)


@app.route('/admin/<id>', methods=['GET', 'POST'])
@login_required
def admin_transaction(id):
    if request.method == 'POST':
        db.session.rollback() # See comment in create_transaction()
        parse_date = lambda s: datetime.datetime.strptime(s, '%d. %b %Y')

        ta = Transaction.query.get(id)

        ta.name = request.form.get('name')
        ta.email = request.form.get('email')
        ta.tel = request.form.get('tel')
        ta.payment = request.form.get('payment')
        ta.delivery = request_or_none('delivery')
        ta.remarks = request_or_none('remarks')

        ta.group = request.form.get('group')

        ta.date_start = parse_date(request_or_none('date_start'))
        ta.date_end = parse_date(request_or_none('date_end'))

        for iid in ta.lend:
            lend = ta.lend[iid] 
            lend.amount = request.form.get('lend_amount_'+iid)
            lend.override_cost = request_or_none('lend_cost_'+iid)
            
            if lend.amount == 0:
                ta.lend.pop(iid)

        for iid in ta.buy:
            buy = ta.buy[iid] 
            buy.amount = request.form.get('buy_amount_'+iid)
            buy.override_cost = request_or_none('buy_cost_'+iid)

            if buy.amount == 0:
                ta.buy.pop(iid)

        db.session.commit()
         
        #flash(u'Änderungen wurden gesichert')

    transactions = Transaction.query.order_by('date desc').all()
    return pjax('admin_transaction.html',
                transactions=transactions,
                eta=Transaction.query.get(id)
                )


@app.route('/admin/<id>/confirm')
@login_required
def admin_transaction_confirm(id):
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    import smtplib

    db.session.rollback() # See comment in create_transaction()
    ta = Transaction.query.get(id)

    msg = MIMEMultipart('alternative')
    msg['Subject'] = u'RAST-SPIELMATERIALVERLEIH: Bestellbestätigung'
    msg['From'] = app.config['EMAIL_ADDRESS']
    msg['To'] = ta.email

    text = render_template('mail_plain.txt', ta=ta)
    html = render_template('mail.html', ta=ta)

    msg.attach( MIMEText(text.encode('utf-8'), 'plain') )
    msg.attach( MIMEText(html.encode('utf-8'), 'html') )

    server = smtplib.SMTP_SSL(app.config['EMAIL_SERVER'], app.config['EMAIL_PORT'])
    server.login(app.config['EMAIL_ADDRESS'],app.config['EMAIL_PASSWORD'])
    server.sendmail(app.config['EMAIL_ADDRESS'],ta.email,msg.as_string())
    server.close()

    # TODO email currently gets sent twice

    ta.progress = 'confirmed'
    db.session.commit()

    return admin_transaction(id)


@app.route('/admin/<id>/return')
@login_required
def admin_transaction_return(id):
    db.session.rollback() # See comment in create_transaction()
    ta = Transaction.query.get(id)

    ta.progress = 'returned'
    db.session.commit()

    return admin_transaction(id)


@app.route('/admin/<id>/delete')
@login_required
def admin_transaction_delete(id):
    db.session.rollback() # See comment in create_transaction()
    ta = Transaction.query.get(id)
    db.session.delete(ta)
    db.session.commit()

    #flash(u'Transaktion gelöscht.')
    return admin()


## Error handlers
# Handle 404 errors
'''
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

# Handle 500 errors
@app.errorhandler(500)
def server_error(e):
    return render_template('500.html'), 500
'''
