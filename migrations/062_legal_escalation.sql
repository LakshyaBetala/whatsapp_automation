-- Legal escalation: what the owner needs on file before a demand notice can go
-- out. Both are optional - app/services/legal.py prints a placeholder for
-- whatever is missing, so the notice draft works on day one and gets better
-- once these are filled in.
--
-- udyam_number decides the whole route. With it the shop is a micro/small
-- enterprise under the MSMED Act 2006: statutory interest under s.16 (three
-- times the RBI bank rate, compounded monthly) and the MSME ODR portal at
-- odr.msme.gov.in. Without it the only route is an advocate's notice, s.12A
-- pre-institution mediation and an Order XXXVII summary suit.
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS udyam_number text;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS shop_address text;

COMMENT ON COLUMN businesses.udyam_number IS
  'Udyam registration number. Set => MSMED s.16 interest + MSME ODR route.';
COMMENT ON COLUMN businesses.shop_address IS
  'Shop address as it should appear on a demand notice.';
