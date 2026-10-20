-- 055_private_invoices.sql
-- Invoice PDFs carry customer names and amounts; nobody should read them by
-- link. ASVA now hands out short-lived signed URLs instead of public ones.
update storage.buckets set public = false where id = 'invoices';
