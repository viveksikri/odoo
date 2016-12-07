# -*- encoding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2010 Tiny SPRL (<http://tiny.be>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

import time
from datetime import datetime
from dateutil.relativedelta import relativedelta

from openerp.osv import fields, osv
import openerp.addons.decimal_precision as dp
from openerp.tools import float_compare
from openerp.tools.translate import _

class account_asset_category(osv.osv):
    _name = 'account.asset.category'
    _description = 'Asset category'

    def _get_full_name(self, cr, uid, ids, name=None, args=None, context=None):
        if context == None:
            context = {}
        res = {}
        for elmt in self.browse(cr, uid, ids, context=context):
            res[elmt.id] = self._get_one_full_name(elmt)
        return res

    def _get_one_full_name(self, elmt, level=6):
        if level <= 0:
            return '...'
        if elmt.parent_id:
            parent_path = self._get_one_full_name(elmt.parent_id, level - 1) + " / "
        else:
            parent_path = ''
        return parent_path + elmt.name

    _columns = {
        'name': fields.char('Name', required=True, select=1),
        'parent_id': fields.many2one('account.asset.category', "Parent Category", domain=[('type','=','view')]),
        'children_ids': fields.one2many('account.asset.category', 'parent_id', 'Account Report'),
        'complete_name': fields.function(_get_full_name, type='char', string='Full Name'),
        'type': fields.selection([('view',"View"),
                                  ('normal',"Normal")], string="Type", required=True),
        'note': fields.text('Note'),
        'account_analytic_id': fields.many2one('account.analytic.account', 'Analytic account'),
        'account_asset_id': fields.many2one('account.account', 'Asset Account', domain=[('type','=','other')]),
        'account_revaluation_id': fields.many2one('account.account', 'Revaluation Account', domain=[('type', '=', 'other')]),
        'account_depreciation_id': fields.many2one('account.account', 'Depreciation Account', domain=[('type','=','other')]),
        'account_expense_depreciation_id': fields.many2one('account.account', 'Depr. Expense Account', domain=[('type','=','other')]),
        'journal_id': fields.many2one('account.journal', 'Journal'),
        'company_id': fields.many2one('res.company', 'Company', required=True),
        'method': fields.selection([('linear','Linear'),
                                    ('degressive','Degressive'),
                                    ('custom','Custom')], 'Computation Method', required=True,
                                   help="Choose the method to use to compute the amount of depreciation lines.\n"\
            "  * Linear: Calculated on basis of: (Gross Value - Salvage Value) / Number of Depreciations\n" \
            "  * Degressive: Calculated on basis of: Book Value * Degressive Factor."),
        'method_number': fields.integer('Number of Depreciations', help="The number of depreciations needed to depreciate your asset"),
        'method_period': fields.integer('Period Length', help="State here the time between 2 depreciations, in months", required=True),
        'method_progress_factor': fields.float('Degressive Factor',
                                               help="Percentage value, for example to express 30% write 30.0"),
        'method_time': fields.selection([('number','Number of Depreciations'),
                                         ('end','Ending Date'),
                                         ('activity', 'Activity'),
                                         ('factor','Factor')], 'Time Method', required=True,
                                  help="Choose the method to use to compute the dates and number of depreciation lines.\n"\
                                       "  * Number of Depreciations: Fix the number of depreciation lines and the time between 2 depreciations.\n" \
                                       "  * Ending Date: Choose the time between 2 depreciations and the date the depreciations won't go beyond\n" \
                                       "  * Activity: Depreciation is a function of use or productivity instead of the passage of time."),
        'method_end': fields.date('Ending date'),
        'method_activity': fields.float('Activity Units'),
        'prorata':fields.boolean('Prorata Temporis', help='Indicates that the first depreciation entry for this asset have to be done from the purchase date instead of the first January'),
        'open_asset': fields.boolean('Skip Draft State', help="Check this if you want to automatically confirm the assets of this category when created by invoices."),
    }

    _defaults = {
        'company_id': lambda self, cr, uid, context: self.pool.get('res.company')._company_default_get(cr, uid, 'account.asset.category', context=context),
        'type': 'normal',
        'method': 'degressive',
        'method_number': 5,
        'method_time': 'number',
        'method_period': 1,
        'method_progress_factor': 30.0,
    }

    def onchange_account_asset(self, cr, uid, ids, account_asset_id, context=None):
        res = {'value':{}}
        if account_asset_id:
           res['value'] = {'account_depreciation_id': account_asset_id, 'account_revaluation_id': account_asset_id}
        return res


class account_asset_asset(osv.osv):
    _name = 'account.asset.asset'
    _description = 'Asset'

    def unlink(self, cr, uid, ids, context=None):
        for asset in self.browse(cr, uid, ids, context=context):
            if asset.account_move_line_ids: 
                raise osv.except_osv(_('Error!'), _('You cannot delete an asset that contains posted depreciation lines.'))
        return super(account_asset_asset, self).unlink(cr, uid, ids, context=context)

    def _get_period(self, cr, uid, context=None):
        periods = self.pool.get('account.period').find(cr, uid, context=context)
        if periods:
            return periods[0]
        else:
            return False

    def _get_last_depreciation_date(self, cr, uid, ids, context=None):
        """
        @param id: ids of a account.asset.asset objects
        @return: Returns a dictionary of the effective dates of the last depreciation entry made for given asset ids. If there isn't any, return the purchase date of this asset
        """
        account_ids = tuple(set([a.account_depreciation_id.id for a in self.browse(cr, uid, ids, context=context)]))
        cr.execute("""
            SELECT a.id as id, COALESCE(MAX(l.date),a.purchase_date) AS date
            FROM account_asset_asset a
            LEFT JOIN account_move_line l ON (l.asset_id = a.id and l.account_id in %s)
            WHERE a.id IN %s
            GROUP BY a.id, a.purchase_date """, (account_ids, tuple(ids)))
        return dict(cr.fetchall())

    def _compute_board_amount(self, cr, uid, asset, i, residual_amount, amount_to_depr, undone_dotation_number, posted_depreciation_line_ids, total_days, depreciation_date, context=None):
        #by default amount = 0
        amount = 0
        if i == undone_dotation_number:
            amount = residual_amount
        else:
            if asset.method == 'linear':
                amount = amount_to_depr / (undone_dotation_number - len(posted_depreciation_line_ids))
                if asset.prorata:
                    amount = amount_to_depr / asset.method_number
                    days = total_days - float(depreciation_date.strftime('%j'))
                    if i == 1:
                        amount = (amount_to_depr / asset.method_number) / total_days * days
                    elif i == undone_dotation_number:
                        amount = (amount_to_depr / asset.method_number) / total_days * (total_days - days)
            elif asset.method == 'degressive':
                amount = residual_amount * asset.method_progress_factor
                if asset.prorata:
                    days = total_days - float(depreciation_date.strftime('%j'))
                    if i == 1:
                        amount = (residual_amount * asset.method_progress_factor) / total_days * days
                    elif i == undone_dotation_number:
                        amount = (residual_amount * asset.method_progress_factor) / total_days * (total_days - days)
        return amount

    def _compute_board_undone_dotation_nb(self, cr, uid, asset, depreciation_date, total_days, context=None):
        undone_dotation_number = asset.method_number
        if asset.method_time == 'end':
            end_date = datetime.strptime(asset.method_end, '%Y-%m-%d')
            undone_dotation_number = 0
            while depreciation_date <= end_date:
                depreciation_date = (datetime(depreciation_date.year, depreciation_date.month, depreciation_date.day) + relativedelta(months=+asset.method_period))
                undone_dotation_number += 1
        if asset.prorata:
            undone_dotation_number += 1
        return undone_dotation_number

    def compute_depreciation_board(self, cr, uid, ids, context=None):
        depreciation_lin_obj = self.pool.get('account.asset.depreciation.line')
        currency_obj = self.pool.get('res.currency')
        for asset in self.browse(cr, uid, ids, context=context):
            if asset.value_residual == 0.0:
                continue
            posted_depreciation_line_ids = depreciation_lin_obj.search(cr, uid, [('asset_id', '=', asset.id), ('state', 'in', ['done','cancel'])],order='depreciation_date desc')
            old_depreciation_line_ids = depreciation_lin_obj.search(cr, uid, [('asset_id', '=', asset.id), ('state', '=', 'draft')])
            if old_depreciation_line_ids:
                depreciation_lin_obj.unlink(cr, uid, old_depreciation_line_ids, context=context)

            residual_amount = asset.method=='linear' and (asset.value_residual + asset.salvage_value) or asset.value_residual
            amount_to_depr = asset.method=='linear' and asset.purchase_value or asset.value_residual
            if asset.prorata:
                depreciation_date = datetime.strptime(self._get_last_depreciation_date(cr, uid, [asset.id], context)[asset.id], '%Y-%m-%d')
            else:
                # depreciation_date = 1st January of purchase year
                purchase_date = datetime.strptime(asset.purchase_date, '%Y-%m-%d')
                #if we already have some previous validated entries, starting date isn't 1st January but last entry + method period
                if (len(posted_depreciation_line_ids)>0):
                    last_depreciation_date = datetime.strptime(depreciation_lin_obj.browse(cr,uid,posted_depreciation_line_ids[0],context=context).depreciation_date, '%Y-%m-%d')
                    depreciation_date = (last_depreciation_date+relativedelta(months=+asset.method_period))
                else:
                    depreciation_date = datetime(purchase_date.year, purchase_date.month, 1)
            day = depreciation_date.day
            month = depreciation_date.month
            year = depreciation_date.year
            total_days = (year % 4) and 365 or 366

            undone_dotation_number = self._compute_board_undone_dotation_nb(cr, uid, asset, depreciation_date, total_days, context=context)
            for x in range(len(posted_depreciation_line_ids), undone_dotation_number):
                i = x + 1
                amount = self._compute_board_amount(cr, uid, asset, i, residual_amount, amount_to_depr, undone_dotation_number, posted_depreciation_line_ids, total_days, depreciation_date, context=context)
                residual_amount -= amount
                vals = {
                     'amount': amount,
                     'asset_id': asset.id,
                     'sequence': i,
                     'name': str(asset.id) +'/' + str(i),
                     'remaining_value': residual_amount,
                     'depreciated_value': (asset.purchase_value - (0.0 if asset.method=='linear' else asset.salvage_value)) - (residual_amount + amount),
                     'depreciation_date': depreciation_date.strftime('%Y-%m-%d'),
                }
                depreciation_lin_obj.create(cr, uid, vals, context=context)
                # Considering Depr. Period as months
                depreciation_date = (datetime(year, month, day) + relativedelta(months=+asset.method_period))
                day = depreciation_date.day
                month = depreciation_date.month
                year = depreciation_date.year
        return True

    def validate(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        return self.write(cr, uid, ids, {
            'state':'open'
        }, context)

    def set_to_close(self, cr, uid, ids, context=None):
        return self.write(cr, uid, ids, {'state': 'close'}, context=context)

    def set_to_draft(self, cr, uid, ids, context=None):
        return self.write(cr, uid, ids, {'state': 'draft'}, context=context)

    def _amount_residual(self, cr, uid, ids, name, args, context=None):
        cr.execute("""SELECT
                l.asset_id as id, SUM(abs(l.debit-l.credit)) AS amount
            FROM
                account_move_line l
            WHERE
                l.asset_id IN %s GROUP BY l.asset_id """, (tuple(ids),))
        res=dict(cr.fetchall())
        for asset in self.browse(cr, uid, ids, context):
            company_currency = asset.company_id.currency_id.id
            current_currency = asset.currency_id.id
            amount = self.pool['res.currency'].compute(cr, uid, company_currency, current_currency, res.get(asset.id, 0.0), context=context)
            res[asset.id] = asset.purchase_value - amount - asset.salvage_value
        for id in ids:
            res.setdefault(id, 0.0)
        return res

    def onchange_company_id(self, cr, uid, ids, company_id=False, context=None):
        val = {}
        if company_id:
            company = self.pool.get('res.company').browse(cr, uid, company_id, context=context)
            if company.currency_id.company_id and company.currency_id.company_id.id != company_id:
                val['currency_id'] = False
            else:
                val['currency_id'] = company.currency_id.id
        return {'value': val}
    
    def onchange_purchase_salvage_value(self, cr, uid, ids, purchase_value, salvage_value, context=None):
        val = {}
        for asset in self.browse(cr, uid, ids, context=context):
            if purchase_value:
                val['value_residual'] = purchase_value - salvage_value
            if salvage_value:
                val['value_residual'] = purchase_value - salvage_value
        return {'value': val}    
    def _entry_count(self, cr, uid, ids, field_name, arg, context=None):
        MoveLine = self.pool('account.move.line')
        return {
            asset_id: MoveLine.search_count(cr, uid, [('asset_id', '=', asset_id)], context=context)
            for asset_id in ids
        }
    _columns = {
        'account_move_line_ids': fields.one2many('account.move.line', 'asset_id', 'Entries', readonly=True, states={'draft':[('readonly',False)]}),
        'entry_count': fields.function(_entry_count, string='# Asset Entries', type='integer'),
        'name': fields.char('Asset Name', required=True, readonly=True, states={'draft':[('readonly',False)]}),
        'code': fields.char('Reference', size=32, readonly=True, states={'draft':[('readonly',False)]}),
        'purchase_value': fields.float('Purchase Value', required=True, readonly=True, states={'draft':[('readonly',False)]}),
        'currency_id': fields.many2one('res.currency','Currency',required=True, readonly=True, states={'draft':[('readonly',False)]}),
        'company_id': fields.many2one('res.company', 'Company', required=True, readonly=True, states={'draft':[('readonly',False)]}),
        'note': fields.text('Note'),
        'category_id': fields.many2one('account.asset.category', 'Asset Category', required=True, change_default=True,
                                       domain=[('type','!=','view')], readonly=True, states={'draft':[('readonly',False)]}),
        'parent_id': fields.many2one('account.asset.asset', 'Parent Asset', readonly=True, states={'draft':[('readonly',False)]}),
        'child_ids': fields.one2many('account.asset.asset', 'parent_id', 'Children Assets', copy=True),
        'buy_date': fields.date('Purchase Date', readonly=True, states={'draft':[('readonly',False)]}),
        'purchase_date': fields.date('Start Date', required=True, readonly=True, states={'draft':[('readonly',False)]},
                                     help="Start the depreciations from this date."),
        'state': fields.selection([('draft','Draft'),('open','Running'),('close','Close')], 'Status', required=True, copy=False,
                                  help="When an asset is created, the status is 'Draft'.\n" \
                                       "If the asset is confirmed, the status goes in 'Running' and the depreciation lines can be posted in the accounting.\n" \
                                       "You can manually close an asset when the depreciation is over. If the last line of depreciation is posted, the asset automatically goes in that status."),
        'active': fields.boolean('Active'),
        'partner_id': fields.many2one('res.partner', 'Partner', readonly=True, states={'draft':[('readonly',False)]}),
        'valuation': fields.selection([('auto', 'Automatic'), ('manual', 'Manual')], 'Valuation Method',
                                   required=True, readonly=True, states={'draft': [('readonly', False)]}, default='auto'),
        'method': fields.selection([('linear','Linear'),
                                    ('degressive','Degressive'),
                                    ('custom','Custom')], 'Computation Method', required=True, readonly=True, states={'draft':[('readonly',False)]}, help="Choose the method to use to compute the amount of depreciation lines.\n"\
            "  * Linear: Calculated on basis of: (Gross Value - Salvage Value) / Number of Depreciations\n" \
            "  * Degressive: Calculated on basis of: Book Value * Degressive Factor"),
        'method_number': fields.integer('Number of Depreciations', readonly=True, states={'draft':[('readonly',False)]}, help="The number of depreciations needed to depreciate your asset"),
        'method_period': fields.integer('Number of Months in a Period', required=True, readonly=True, states={'draft':[('readonly',False)]}, help="The amount of time between two depreciations, in months"),
        'method_end': fields.date('Ending Date', readonly=True, states={'draft':[('readonly',False)]}),
        'method_activity': fields.float('Activity Units', readonly=True, states={'draft':[('readonly',False)]}),
        'method_custom': fields.float('Custom Amount', readonly=True, states={'draft': [('readonly', False)]}),
        'method_progress_factor': fields.float('Degressive Factor', readonly=True, states={'draft':[('readonly',False)]},
                                               help="Percentage value, for example to express 30% write 30.0"),
        'value_residual': fields.function(_amount_residual, method=True, digits_compute=dp.get_precision('Account'), string='Residual Value'),
        'method_time': fields.selection([('number','Number of Depreciations'),
                                         ('end','Ending Date'),
                                         ('activity','Activity'),
                                         ('factor', 'Factor')], 'Time Method', required=True, readonly=True, states={'draft':[('readonly',False)]},
                                  help="Choose the method to use to compute the dates and number of depreciation lines.\n"\
                                       "  * Number of Depreciations: Fix the number of depreciation lines and the time between 2 depreciations.\n" \
                                       "  * Ending Date: Choose the time between 2 depreciations and the date the depreciations won't go beyond\n" \
                                       "  * Activity: Depreciation is a function of use or productivity instead of the passage of time."),
        'prorata':fields.boolean('Prorata Temporis', readonly=True, states={'draft':[('readonly',False)]}, help='Indicates that the first depreciation entry for this asset have to be done from the purchase date instead of the first January'),
        'history_ids': fields.one2many('account.asset.history', 'asset_id', 'History', readonly=False),
        'depreciation_line_ids': fields.one2many('account.asset.depreciation.line', 'asset_id', 'Depreciation Lines', readonly=True, states={'draft':[('readonly',False)],'open':[('readonly',False)]}),
        'salvage_value': fields.float('Salvage Value', digits_compute=dp.get_precision('Account'), help="It is the amount you plan to have that you cannot depreciate.", readonly=True, states={'draft':[('readonly',False)]}),
        'account_analytic_id': fields.many2one('account.analytic.account', 'Analytic account', readonly=True, states={'draft':[('readonly',False)]}),
        'journal_id': fields.many2one('account.journal', 'Journal', domain=[('type', '=', 'general')],
                                            readonly=True, states={'draft':[('readonly',False)]}, required=False),
        'account_asset_id': fields.many2one('account.account', 'Asset Account', domain=[('type', '=', 'other')],
                                            readonly=True, states={'draft':[('readonly',False)]}, required=False),
        'account_revaluation_id': fields.many2one('account.account', 'Revaluation Account', readonly=True,
                                                  domain=[('type', '=', 'other')], required=False,
                                                  states={'draft':[('readonly',False)]}),
        'account_depreciation_id': fields.many2one('account.account', 'Depreciation Account', readonly=True,
                                                   states={'draft':[('readonly',False)]}, required=False,
                                                   domain=[('type', '=', 'other')]),
        'account_expense_depreciation_id': fields.many2one('account.account', 'Depr. Expense Account', readonly=True,
                                                           states={'draft':[('readonly',False)]}, required=False,
                                                           domain=[('type', '=', 'other')]),

    }
    _defaults = {
        'purchase_date': lambda obj, cr, uid, context: time.strftime('%Y-%m-%d'),
        'active': True,
        'state': 'draft',
        'method': 'degressive',
        'method_time': 'number',
        'method_period': 1,
        'method_progress_factor': 30.0,
        'prorata': True,
        'currency_id': lambda self,cr,uid,c: self.pool.get('res.users').browse(cr, uid, uid, c).company_id.currency_id.id,
        'company_id': lambda self, cr, uid, context: self.pool.get('res.company')._company_default_get(cr, uid, 'account.asset.asset',context=context),
    }

    def _check_recursion(self, cr, uid, ids, context=None, parent=None):
        return super(account_asset_asset, self)._check_recursion(cr, uid, ids, context=context, parent=parent)

    def _check_prorata(self, cr, uid, ids, context=None):
        for asset in self.browse(cr, uid, ids, context=context):
            if asset.prorata and asset.method_time != 'number':
                return False
        return True

    _constraints = [
        (_check_recursion, 'Error ! You cannot create recursive assets.', ['parent_id']),
        #(_check_prorata, 'Prorata temporis can be applied only for time method "number of depreciations".', ['prorata']),
    ]

    def name_get(self, cr, uid, ids, context=None):
        if not ids:
            return []
        if isinstance(ids, (int, long)):
            ids = [ids]
        reads = self.read(cr, uid, ids, ['name', 'code'], context=context)
        res = []
        for record in reads:
            name = record['name']
            if record['code']:
                name = record['code'] + ' ' + name
            res.append((record['id'], name))
        return res

    def onchange_category_id(self, cr, uid, ids, category_id, context=None):
        res = {'value':{}}
        asset_categ_obj = self.pool.get('account.asset.category')
        if category_id:
            category_obj = asset_categ_obj.browse(cr, uid, category_id, context=context)
            res['value'] = {
                            'method': category_obj.method,
                            'method_number': category_obj.method_number,
                            'method_time': category_obj.method_time,
                            'method_period': category_obj.method_period,
                            'method_progress_factor': category_obj.method_progress_factor,
                            'method_end': category_obj.method_end,
                            'method_activity': category_obj.method_activity,
                            'prorata': category_obj.prorata,
                            'account_analytic_id': category_obj.account_analytic_id.id,
                            'account_asset_id': category_obj.account_asset_id.id,
                            'account_revaluation_id': category_obj.account_revaluation_id.id,
                            'account_depreciation_id': category_obj.account_depreciation_id.id,
                            'account_expense_depreciation_id': category_obj.account_expense_depreciation_id.id,
            }
        return res

    def onchange_method_time(self, cr, uid, ids, method_time='number', context=None):
        res = {'value': {}}
        # if method_time != 'number':
        #     res['value'] = {'prorata': False}
        return res

    def _compute_entries(self, cr, uid, ids, period_id, context=None):
        result = []
        period_obj = self.pool.get('account.period')
        depreciation_obj = self.pool.get('account.asset.depreciation.line')
        period = period_obj.browse(cr, uid, period_id, context=context)
        depreciation_ids = depreciation_obj.search(cr, uid, [('asset_id', 'in', ids), ('depreciation_date', '<=', period.date_stop), ('depreciation_date', '>=', period.date_start), ('state', '=', 'draft')], context=context)
        context = dict(context or {}, depreciation_date=period.date_stop)
        return depreciation_obj.create_move(cr, uid, depreciation_ids, context=context)

    def create(self, cr, uid, vals, context=None):
        if not vals.get('code', False):
            vals['code'] = self.pool.get('ir.sequence').get(cr, uid, 'account.asset.code')
        asset_id = super(account_asset_asset, self).create(cr, uid, vals, context=context)
        #self.compute_depreciation_board(cr, uid, [asset_id], context=context)
        return asset_id
    
    def open_entries(self, cr, uid, ids, context=None):
        context = dict(context or {}, search_default_asset_id=ids, default_asset_id=ids)
        return {
            'name': _('Journal Items'),
            'view_type': 'form',
            'view_mode': 'tree,form',
            'res_model': 'account.move.line',
            'view_id': False,
            'type': 'ir.actions.act_window',
            'context': context,
        }


class account_asset_depreciation_line(osv.osv):
    _name = 'account.asset.depreciation.line'
    _description = 'Asset depreciation line'

    _columns = {
        'name': fields.char('Depreciation Name', required=False, select=1, readonly=True, states={'draft':[('readonly',False)]}),
        'sequence': fields.integer('Sequence', required=False, readonly=True, states={'draft':[('readonly',False)]}),
        'asset_id': fields.many2one('account.asset.asset', 'Asset', required=True, ondelete='cascade',readonly=True, states={'draft':[('readonly',False)]}),
        'parent_state': fields.related('asset_id', 'state', type='char', string='State of Asset', readonly=True),
        'amount': fields.float('Current Depreciation', digits_compute=dp.get_precision('Account'), required=True, readonly=True, states={'draft':[('readonly',False)]}),
        'remaining_value': fields.float('Next Period Depreciation', digits_compute=dp.get_precision('Account'),required=True,readonly=True, states={'draft':[('readonly',False)]}),
        'depreciated_value': fields.float('Amount Already Depreciated', required=True,readonly=True, states={'draft':[('readonly',False)]}),
        'depreciation_date': fields.date('Depreciation Date', select=1, required=True, readonly=True, states={'draft':[('readonly',False)]}),
        'move_id': fields.many2one('account.move', 'Depreciation Entry',
                                   readonly=True, states={'draft':[('readonly',False)]}),
        'period_id': fields.many2one('account.period', 'Period', domain=[('special','=',False)],
                                   readonly=True, states={'draft': [('readonly', False)]}),
        'state': fields.selection([('draft','Draft'),
                                   ('done','Done'),
                                   ('cancel','Cancel')], string='Posted Save', readonly=True),
    }
    _defaults = {
        'state': 'draft',
    }
    _order = "depreciation_date desc,sequence asc"

    def action_draft(self, cr, uid, ids, context=None):
        return self.write(cr, uid, ids, {'state': 'draft'}, context=context)

    def action_cancel(self, cr, uid, ids, context=None):
        move_obj = self.pool.get('account.move')
        move_ids = [l.move_id.id for l in self.browse(cr, uid, ids, context=context) if l.move_id]
        move_obj.button_cancel(cr, uid, move_ids, context=context)
        move_obj.unlink(cr, uid, move_ids, context=context)
        return self.write(cr, uid, ids, {'state': 'cancel'}, context=context)

    def action_done(self, cr, uid, ids, context=None):
        asset_obj = self.pool.get('account.asset.asset')
        asset_ids = list(set([l.asset_id.id for l in self.browse(cr, uid, ids, context=context)]))
        for asset in asset_obj.browse(cr, uid, asset_ids, context=context):
            if asset.state <> 'open':
                raise osv.except_osv(_('Error!'),
                                     _('The asset %s must be in open state.')%(asset.name,))
        self.create_move(cr, uid, ids, context=context)
        return self.write(cr, uid, ids, {'state': 'done'}, context=context)

    def create_move_lines(self, cr, uid, move_id, line, amount, depreciation_date, period_id, context=None):
        move_line_obj = self.pool.get('account.move.line')
        prec = self.pool['decimal.precision'].precision_get(cr, uid, 'Account')
        journal_id = line.asset_id.category_id.journal_id.id
        partner_id = line.asset_id.partner_id.id
        company_currency = line.asset_id.company_id.currency_id.id
        current_currency = line.asset_id.currency_id.id
        move_line_obj.create(cr, uid, {
            'name': _("%s Acumulated Depreciation")%(line.name or line.depreciation_date,),
            'ref': line.asset_id.name,
            'move_id': move_id,
            'account_id': line.asset_id.account_depreciation_id.id or line.asset_id.category_id.account_depreciation_id.id,
            'debit': 0.0 if float_compare(amount, 0.0, precision_digits=prec) > 0 else -amount,
            'credit': amount if float_compare(amount, 0.0, precision_digits=prec) > 0 else 0.0,
            'period_id': period_id,
            'journal_id': journal_id,
            'partner_id': partner_id,
            'currency_id': company_currency != current_currency and current_currency or False,
            'amount_currency': company_currency != current_currency and -1 * line.amount or 0.0,
            'analytic_account_id': line.asset_id.account_analytic_id.id or line.asset_id.category_id.account_analytic_id.id,
            'date': depreciation_date,
            'asset_id': line.asset_id.id
        })
        move_line_obj.create(cr, uid, {
            'name': _("%s Expense Depreciation")%(line.name or line.depreciation_date,),
            'ref': line.asset_id.name,
            'move_id': move_id,
            'account_id': line.asset_id.account_expense_depreciation_id.id or line.asset_id.category_id.account_expense_depreciation_id.id,
            'credit': 0.0 if float_compare(amount, 0.0, precision_digits=prec) > 0 else -amount,
            'debit': amount if float_compare(amount, 0.0, precision_digits=prec) > 0 else 0.0,
            'period_id': period_id,
            'journal_id': journal_id,
            'partner_id': partner_id,
            'currency_id': company_currency != current_currency and current_currency or False,
            'amount_currency': company_currency != current_currency and line.amount or 0.0,
            'analytic_account_id': line.asset_id.account_analytic_id.id or line.asset_id.category_id.account_analytic_id.id,
            'date': depreciation_date,
            'asset_id': line.asset_id.id
        })


    def create_move(self, cr, uid, ids, context=None):
        context = dict(context or {})
        can_close = False
        asset_obj = self.pool.get('account.asset.asset')
        period_obj = self.pool.get('account.period')
        move_obj = self.pool.get('account.move')

        currency_obj = self.pool.get('res.currency')
        created_move_ids = []
        asset_ids = []
        for line in self.browse(cr, uid, ids, context=context):
            if line.asset_id.valuation != 'auto':
                continue
            depreciation_date = context.get('depreciation_date') or line.depreciation_date or time.strftime('%Y-%m-%d')
            if line.period_id:
                period_id = line.period_id.id
            else:
                period_ids = period_obj.find(cr, uid, depreciation_date, context=context)
                period_id = period_ids and period_ids[0] or False
            company_currency = line.asset_id.company_id.currency_id.id
            current_currency = line.asset_id.currency_id.id
            context.update({'date': depreciation_date})
            amount = currency_obj.compute(cr, uid, current_currency, company_currency, line.amount, context=context)
            sign = (line.asset_id.category_id.journal_id.type == 'purchase' and 1) or -1
            asset_name = "/"
            reference = line.asset_id.name
            move_vals = {
                'name': asset_name,
                'date': depreciation_date,
                'ref': reference,
                'period_id': period_id,
                'journal_id': line.asset_id.journal_id.id or line.asset_id.category_id.journal_id.id,
                }
            move_id = move_obj.create(cr, uid, move_vals, context=context)
            self.create_move_lines(cr, uid, move_id, line, amount, depreciation_date, period_id, context=context)
            self.write(cr, uid, line.id, {'move_id': move_id}, context=context)
            created_move_ids.append(move_id)
            asset_ids.append(line.asset_id.id)
        # we re-evaluate the assets to determine whether we can close them
        for asset in asset_obj.browse(cr, uid, list(set(asset_ids)), context=context):
            if currency_obj.is_zero(cr, uid, asset.currency_id, asset.value_residual):
                asset.write({'state': 'close'})
        return created_move_ids


class account_move_line(osv.osv):
    _inherit = 'account.move.line'
    _columns = {
        'asset_id': fields.many2one('account.asset.asset', 'Asset', ondelete="restrict"),
    }

class account_asset_history(osv.osv):
    _name = 'account.asset.history'
    _description = 'Asset history'
    _columns = {
        'name': fields.char('History name', select=1),
        'user_id': fields.many2one('res.users', 'User', required=True),
        'date': fields.date('Date', required=True, select=1),
        'asset_id': fields.many2one('account.asset.asset', 'Asset', required=True),
        'method_time': fields.selection([('number','Number of Depreciations'),('end','Ending Date')], 'Time Method',
                                  help="The method to use to compute the dates and number of depreciation lines.\n"\
                                       "Number of Depreciations: Fix the number of depreciation lines and the time between 2 depreciations.\n" \
                                       "Ending Date: Choose the time between 2 depreciations and the date the depreciations won't go beyond."),
        'method_number': fields.integer('Number of Depreciations', help="The number of depreciations needed to depreciate your asset"),
        'method_period': fields.integer('Period Length', help="Time in month between two depreciations"),
        'method_end': fields.date('Ending date'),
        'note': fields.text('Note'),
    }
    _order = 'date desc'
    _defaults = {
        'date': lambda *args: time.strftime('%Y-%m-%d'),
        'user_id': lambda self, cr, uid, ctx: uid
    }


# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
