"""Partner / Reseller / Affiliate program.

Partners are ordinary ``users`` accounts signed in with a ``partner`` session
scope; customers they bring are ordinary organizations (demo -> subscription)
linked by one ``partner_referrals`` record per organization. Commissions are
generated from the existing subscription lifecycle (confirmation, renewal);
there is no second customer, billing or payment system.
"""
