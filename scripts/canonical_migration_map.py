"""Explicit offline translations from legacy field names to Genie paths.

This module is migration/test tooling only.  It is deliberately outside the
production ``src.canonical`` runtime and never acts as a runtime alias table.
"""

from dataclasses import dataclass
import re
import unicodedata

from src.canonical.paths import parse_path


@dataclass(frozen=True)
class Translation:
    path: str
    fragment: str | None = None
    product_gate: str | None = None


@dataclass(frozen=True)
class _LegacyField:
    translation: Translation
    label: str


def _entry(path: str, label: str, fragment: str | None = None) -> _LegacyField:
    return _LegacyField(Translation(path=path, fragment=fragment), label)


# Every entry is a semantic decision.  In particular, Lead fields are not
# mechanically converted to Opportunity fields and account/contact/deal data
# remain distinct.  Suffix handling below only adds metadata to these entries.
_LEGACY_FIELDS = {
    # Contact: identity, contact data, employment, finance, and CRM metadata.
    "Contact.AccountId": _entry("deal.account_id", "Account azonosító"),
    "Contact.Account_holding_bank__c": _entry("participant.borrower.account_holding_bank", "Számlavezető bank"),
    "Contact.Affordable_monthly_installments__c": _entry("participant.borrower.affordable_monthly_installments", "Vállalható havi törlesztő"),
    "Contact.AssistantName": _entry("participant.borrower.assistant_name", "Asszisztens neve"),
    "Contact.AssistantPhone": _entry("participant.borrower.assistant_phone", "Asszisztens telefonszáma"),
    "Contact.Average_monthly_net_income__c": _entry("participant.borrower.monthly_income", "Havi nettó jövedelem"),
    "Contact.Bank__c": _entry("participant.borrower.bank_name", "Bank"),
    "Contact.Banki_ugyintezo_fiok__c": _entry("participant.borrower.bank_branch", "Bankfiók"),
    "Contact.Banki_ugyintezo_pozicio__c": _entry("participant.borrower.bank_contact_position", "Banki ügyintéző pozíciója"),
    "Contact.Banki_ugyintezo_terulet__c": _entry("participant.borrower.bank_contact_region", "Banki ügyintéző területe"),
    "Contact.Birthdate": _entry("participant.borrower.birth_date", "Születési dátum"),
    "Contact.Birthdate_year": _entry("participant.borrower.birth_date", "Születési év", "year"),
    "Contact.Birthdate_month": _entry("participant.borrower.birth_date", "Születési hónap", "month"),
    "Contact.Birthdate_day": _entry("participant.borrower.birth_date", "Születési nap", "day"),
    "Contact.Cafeteria_bonus__c": _entry("participant.borrower.cafeteria_bonus", "Cafeteria, bónusz"),
    "Contact.Campaign__c": _entry("deal.campaign", "Kampány"),
    "Contact.Citizenship__c": _entry("participant.borrower.citizenship", "Állampolgárság"),
    "Contact.Concern__c": _entry("participant.borrower.concern", "Aggály"),
    "Contact.Continuous_TB_2_years__c": _entry("participant.borrower.continuous_tb_2_years", "OEP/TB jogviszony két éve"),
    "Contact.Contract_end_date__c": _entry("participant.borrower.contract_end_date", "Határozott szerződés vége"),
    "Contact.Contract_start_date__c": _entry("participant.borrower.contract_start_date", "Szerződés kezdete"),
    "Contact.Current_debt_Credit_limit__c": _entry("participant.borrower.current_debt_credit_limit", "Fennálló hitelek részletezve"),
    "Contact.Current_employment_started__c": _entry("participant.borrower.current_employment_started", "Jelenlegi munkaviszony kezdete"),
    "Contact.Date_of_birth__c": _entry("participant.borrower.birth_date", "Születési dátum"),
    "Contact.Date_of_handover__c": _entry("participant.borrower.date_of_handover", "Átadás dátuma"),
    "Contact.Date_of_notification_for_residence__c": _entry("participant.borrower.residence_notification_date", "Lakcím bejelentésének dátuma"),
    "Contact.Date_of_signature__c": _entry("participant.borrower.date_of_signature", "Aláírás dátuma"),
    "Contact.Date_of_transfer__c": _entry("participant.borrower.date_of_transfer", "Utalás dátuma"),
    "Contact.Department": _entry("participant.borrower.department", "Osztály"),
    "Contact.Dependents_count__c": _entry("participant.borrower.dependents_count", "Eltartottak száma"),
    "Contact.Description": _entry("participant.borrower.description", "Szereplő megjegyzése"),
    "Contact.Description_of_loan_purpose_comments__c": _entry("participant.borrower.description_of_loan_purpose", "Hitelcél megjegyzése"),
    "Contact.Display_Name__c": _entry("participant.borrower.display_name", "Megjelenített név"),
    "Contact.Divident__c": _entry("participant.borrower.divident", "Osztalék"),
    "Contact.Email": _entry("participant.borrower.email", "E-mail cím"),
    "Contact.EmailBouncedDate": _entry("participant.borrower.email_bounced_date", "E-mail visszapattanás dátuma"),
    "Contact.EmailBouncedReason": _entry("participant.borrower.email_bounced_reason", "E-mail visszapattanás oka"),
    "Contact.Employee_registration_c__c": _entry("participant.borrower.employee_registration", "Munkavállalói regisztráció"),
    "Contact.Employer_s_company_type__c": _entry("participant.borrower.employer_company_type", "Munkáltató cégformája"),
    "Contact.Employment_Type_c__c": _entry("participant.borrower.employment_type", "Munkaviszony típusa"),
    "Contact.Estimated_market_value__c": _entry("property.estimated_value", "Becsült piaci érték"),
    "Contact.Fax": _entry("participant.borrower.fax", "Fax"),
    "Contact.Financial_Institution__c": _entry("participant.borrower.financial_institution", "Pénzintézet"),
    "Contact.FirstName": _entry("participant.borrower.first_name", "Keresztnév"),
    "Contact.First_Co_debtor__c": _entry("participant.co_borrower[1].contact_id", "Első adóstárs Contact azonosítója"),
    "Contact.Foglalkozas_tipusa__c": _entry("participant.borrower.foglalkozas_tipusa", "Foglalkozás típusa"),
    "Contact.HasOptedOutOfEmail": _entry("participant.borrower.email_opt_out", "E-mail leiratkozás"),
    "Contact.Highest_Educational_Qualification__c": _entry("participant.borrower.education", "Legmagasabb iskolai végzettség"),
    "Contact.HomePhone": _entry("participant.borrower.home_phone", "Otthoni telefon"),
    "Contact.ID_Card_Number__c": _entry("participant.borrower.personal_id", "Személyazonosító igazolvány száma"),
    "Contact.Personal_ID__c": _entry("participant.borrower.personal_number", "Személyi szám"),
    "Contact.Address_Card_Number__c": _entry("participant.borrower.id_card_number", "Lakcímkártya száma"),
    "Contact.Income_from_self_owned_company__c": _entry("participant.borrower.income_from_self_owned", "Saját cégből származó jövedelem"),
    "Contact.Income_type__c": _entry("participant.borrower.income_type", "Jövedelem típusa"),
    "Contact.IndividualId": _entry("participant.borrower.individual_id", "Individual azonosító"),
    "Contact.Industry__c": _entry("participant.borrower.industry", "Iparág"),
    "Contact.Interest_Period__c": _entry("deal.interest_period", "Kamatperiódus"),
    "Contact.Interest_rate__c": _entry("deal.interest_rate", "Kamatláb"),
    "Contact.IsEmailBounced": _entry("participant.borrower.is_email_bounced", "Visszapattanó e-mail"),
    "Contact.IsPriorityRecord": _entry("participant.borrower.is_priority_record", "Prioritásos rekord"),
    "Contact.Jovairas_vallalasa__c": _entry("participant.borrower.jovairas_vallalasa", "Jóváírás vállalása"),
    "Contact.LastName": _entry("participant.borrower.last_name", "Vezetéknév"),
    "Contact.Last_4_nr_of_ID__c": _entry("participant.borrower.personal_id_last4", "Igazolványszám utolsó négy jegye"),
    "Contact.LeadSource": _entry("deal.lead_source", "Lead forrása"),
    "Contact.Lead_Priority__c": _entry("deal.lead_priority", "Lead prioritása"),
    "Contact.Legal_Title_of_permanent_residence__c": _entry("participant.borrower.legal_title", "Lakóhely jogcíme"),
    "Contact.Loan_Purpose__c": _entry("deal.loan_purpose", "Hitel célja"),
    "Contact.Loan_amount__c": _entry("deal.loan_amount", "Hitelösszeg"),
    "Contact.Loan_period__c": _entry("deal.loan_term_months", "Futamidő hónapokban"),
    "Contact.MailingCity": _entry("participant.borrower.mailing_address.city", "Levelezési település"),
    "Contact.MailingCountry": _entry("participant.borrower.mailing_country", "Levelezési ország"),
    "Contact.MailingCountryCode": _entry("participant.borrower.mailing_country_code", "Levelezési ország kódja"),
    "Contact.MailingPostalCode": _entry("participant.borrower.mailing_address.zip_code", "Levelezési irányítószám"),
    "Contact.MailingState": _entry("participant.borrower.mailing_state", "Levelezési megye"),
    "Contact.MailingStateCode": _entry("participant.borrower.mailing_state_code", "Levelezési megye kódja"),
    "Contact.MailingStreet": _entry("participant.borrower.mailing_address.street", "Levelezési utca"),
    "Contact.Marital_Status__c": _entry("participant.borrower.marital_status", "Családi állapot"),
    "Contact.Missing_documents__c": _entry("deal.missing_documents", "Hiányzó dokumentumok"),
    "Contact.MobilePhone": _entry("participant.borrower.mobile_phone", "Mobiltelefonszám"),
    "Contact.Monthly_Payment_details__c": _entry("participant.borrower.monthly_payment_details", "Havi törlesztés részletei"),
    "Contact.Monthly_installment_payment__c": _entry("participant.borrower.existing_loan[1].monthly_payment", "Meglévő hitel havi törlesztője"),
    "Contact.Mortgagor__c": _entry("deal.has_mortgagor", "Zálogkötelezett jelenléte"),
    "Contact.Mother_s_Name__c": _entry("participant.borrower.mother_name", "Anyja születési neve"),
    "Contact.Name": _entry("participant.borrower.name", "Teljes név"),
    "Contact.Name_of_employer__c": _entry("participant.borrower.employer", "Munkáltató neve"),
    "Contact.Nem_preferalt_bank__c": _entry("participant.borrower.non_preferred_bank", "Nem preferált bank"),
    "Contact.Next_step__c": _entry("deal.next_step", "Következő lépés"),
    "Contact.Numer_and_Age_of_Children__c": _entry("participant.borrower.children_details", "Gyermekek száma és életkora"),
    "Contact.Occupation__c": _entry("participant.borrower.occupation", "Foglalkozás"),
    "Contact.OtherCity": _entry("participant.borrower.address.city", "Állandó lakcím települése"),
    "Contact.OtherCountry": _entry("participant.borrower.other_country", "Egyéb cím országa"),
    "Contact.OtherCountryCode": _entry("participant.borrower.other_country_code", "Egyéb cím országkódja"),
    "Contact.OtherPhone": _entry("participant.borrower.other_phone", "Egyéb telefonszám"),
    "Contact.OtherPostalCode": _entry("participant.borrower.address.zip_code", "Állandó lakcím irányítószáma"),
    "Contact.OtherState": _entry("participant.borrower.other_state", "Egyéb cím megyéje"),
    "Contact.OtherStateCode": _entry("participant.borrower.other_state_code", "Egyéb cím megyekódja"),
    "Contact.OtherStreet": _entry("participant.borrower.address.street", "Állandó lakcím utcája"),
    "Contact.Other_income__c": _entry("participant.borrower.other_income", "Egyéb jövedelem"),
    "Contact.Other_monthly_deductions__c": _entry("participant.borrower.other_monthly_deductions", "Egyéb havi levonások"),
    "Contact.Other_monthly_income__c": _entry("participant.borrower.other_monthly_income", "Egyéb havi jövedelem"),
    "Contact.Own_resources__c": _entry("deal.down_payment", "Önerő"),
    "Contact.OwnerId": _entry("participant.borrower.owner_id", "Tulajdonos azonosítója"),
    "Contact.Ownership_in_employer__c": _entry("participant.borrower.ownership_in_employer", "Tulajdonrész a munkáltatóban"),
    "Contact.Partneri_bank__c": _entry("participant.borrower.partner_bank", "Partneri bank"),
    "Contact.Permanent_address__c": _entry("participant.borrower.permanent_address", "Állandó lakcím"),
    "Contact.Phone": _entry("participant.borrower.phone", "Telefonszám"),
    "Contact.Place_of_Birth__c": _entry("participant.borrower.birth_place", "Születési hely"),
    "Contact.Preferalt_bank__c": _entry("participant.borrower.preferalt_bank", "Preferált bank"),
    "Contact.Property_50pct_ownership_details__c": _entry("participant.borrower.property_50pct_ownership_details", "50%-ot meghaladó lakóingatlan-tulajdon"),
    "Contact.Property_Type__c": _entry("property.property_type", "Ingatlan típusa"),
    "Contact.Property_encumbrances__c": _entry("property.terhek", "Ingatlan terhek"),
    "Contact.Property_value__c": _entry("property.estimated_value", "Becsült piaci érték"),
    "Contact.Purchase_price_construction_cost__c": _entry("deal.purchase_price", "Vételár vagy építési költség"),
    "Contact.Re_payment_fee__c": _entry("deal.repayment_fee", "Előtörlesztési díj"),
    "Contact.Referral_fee_percentage__c": _entry("deal.referral_fee_percentage", "Ajánlói díj százaléka"),
    "Contact.Referral_name__c": _entry("deal.referrer_name", "Ajánló neve"),
    "Contact.Regular_referral__c": _entry("deal.regular_referral", "Rendszeres ajánlás"),
    "Contact.Related_Bank__c": _entry("deal.related_bank", "Kapcsolódó bank"),
    "Contact.Relation__c": _entry("participant.borrower.relation", "Kapcsolat típusa"),
    "Contact.ReportsToId": _entry("participant.borrower.reports_to_id", "Vezető azonosítója"),
    "Contact.Residence_Status__c": _entry("participant.borrower.residence_status", "Lakóhelyi jogviszony"),
    "Contact.Salutation": _entry("participant.borrower.salutation", "Megszólítás"),
    "Contact.Second_Co_debtor__c": _entry("participant.co_borrower[2].contact_id", "Második adóstárs Contact azonosítója"),
    "Contact.Secondary_Email__c": _entry("participant.borrower.secondary_email", "Másodlagos e-mail cím"),
    "Contact.Self_employment_details__c": _entry("participant.borrower.self_employment_details", "Egyéni vállalkozás részletei"),
    "Contact.Session_ID__c": _entry("deal.session_id", "Munkamenet azonosító"),
    "Contact.State_Support__c": _entry("participant.borrower.state_support", "Állami támogatás"),
    "Contact.Szuletesi_nev__c": _entry("participant.borrower.birth_name", "Születési név"),
    "Contact.Tax_ID__c": _entry("participant.borrower.tax_id", "Adóazonosító jel"),
    "Contact.Term_in_year_c": _entry("deal.loan_term_years", "Futamidő években"),
    "Contact.Term_in_year_c__c": _entry("deal.loan_term_years", "Futamidő években"),
    "Contact.Third_Co_debtor__c": _entry("participant.co_borrower[3].contact_id", "Harmadik adóstárs Contact azonosítója"),
    "Contact.Title": _entry("participant.borrower.title", "Titulus"),
    "Contact.Town_or_possibly_the_exact_address__c": _entry("property.town_or_exact_address", "Ingatlan települése vagy pontos címe"),
    "Contact.Usufructuary__c": _entry("deal.has_usufructuary", "Haszonélvező jelenléte"),
    "Contact.What_type_of_loan__c": _entry("deal.product_type", "Hitel típusa"),
    "Contact.X2_Affordable_monthly_installments__c": _entry("deal.secondary_affordable_monthly_installments", "Második hitel vállalható törlesztője"),
    "Contact.X2_Interest_Period__c": _entry("deal.secondary_interest_period", "Második hitel kamatperiódusa"),
    "Contact.X2_Loan_Purpose__c": _entry("deal.secondary_loan_purpose", "Második hitel célja"),
    "Contact.X2_Loan_amount__c": _entry("deal.secondary_loan_amount", "Második hitel összege"),
    "Contact.X2_Term_in_year__c": _entry("deal.secondary_loan_term_years", "Második hitel futamideje években"),
    "Contact.X2_What_type_of_loan__c": _entry("deal.secondary_product_type", "Második hitel típusa"),
    "Contact.X3_Affordable_monthly_installments__c": _entry("deal.tertiary_affordable_monthly_installments", "Harmadik hitel vállalható törlesztője"),
    "Contact.X3_Interest_Period__c": _entry("deal.tertiary_interest_period", "Harmadik hitel kamatperiódusa"),
    "Contact.X3_Loan_Purpose__c": _entry("deal.tertiary_loan_purpose", "Harmadik hitel célja"),
    "Contact.X3_Loan_amount__c": _entry("deal.tertiary_loan_amount", "Harmadik hitel összege"),
    "Contact.X3_Term_in_year__c": _entry("deal.tertiary_loan_term_years", "Harmadik hitel futamideje években"),
    "Contact.X3_What_type_of_loan__c": _entry("deal.tertiary_product_type", "Harmadik hitel típusa"),
    "Contact.Year_of_birthdate__c": _entry("participant.borrower.birth_date", "Születési év", "year"),
    "Contact.ZIP__c": _entry("participant.borrower.address.zip_code", "Irányítószám"),
    # Lead: general lead data and explicitly property-scoped fields.
    "Lead.AnnualRevenue": _entry("deal.annual_revenue", "Éves árbevétel"),
    "Lead.City": _entry("participant.borrower.address.city", "Település"),
    "Lead.Company": _entry("participant.borrower.employer", "Munkáltató neve"),
    "Lead.Country": _entry("participant.borrower.country", "Ország"),
    "Lead.CountryCode": _entry("participant.borrower.country_code", "Országkód"),
    "Lead.Country__c": _entry("participant.borrower.country", "Ország"),
    "Lead.Egyeb_forras_info__c": _entry("deal.other_source_info", "Egyéb forrás információ"),
    "Lead.Elmult_90_napos_hiteligenyles__c": _entry("deal.recent_loan_application_90_days", "Elmúlt 90 napos hiteligénylés"),
    "Lead.Employee_registration__c": _entry("participant.borrower.employee_registration", "Munkavállalói regisztráció"),
    "Lead.Employment_Type__c": _entry("participant.borrower.employment_type", "Munkaviszony típusa"),
    "Lead.Estimated__c": _entry("property.estimated_value", "Becsült piaci érték"),
    "Lead.Form_Type__c": _entry("property.form_type", "Építmény típusa"),
    "Lead.Industry": _entry("participant.borrower.industry", "Iparág"),
    "Lead.Ingatlan_Kozterulet_jellege": _entry("property.street_type", "Közterület jellege"),
    "Lead.Ingatlan_Kozterulet_jellege__c": _entry("property.street_type", "Közterület jellege"),
    "Lead.Ingatlan_alapterulet__c": _entry("property.area_sqm", "Hasznos alapterület"),
    "Lead.Ingatlan_emelet__c": _entry("property.address.floor", "Ingatlan emelete"),
    "Lead.Ingatlan_energetika__c": _entry("property.energetika", "Energetikai besorolás"),
    "Lead.Ingatlan_hazszam__c": _entry("property.address.house_number", "Ingatlan házszáma"),
    "Lead.Ingatlan_irsz__c": _entry("property.address.zip_code", "Ingatlan irányítószáma"),
    "Lead.Ingatlan_jellege__c": _entry("property.property_type", "Ingatlan típusa"),
    "Lead.Ingatlan_kozterulet_neve__c": _entry("property.address.street", "Ingatlan utcája"),
    "Lead.Ingatlan_megjegyzes__c": _entry("property.parcel_number", "Helyrajzi szám"),
    "Lead.Ingatlan_szerepe__c": _entry("property.ingatlan_szerepe", "Ingatlan szerepe"),
    "Lead.Ingatlan_telepules__c": _entry("property.address.city", "Ingatlan települése"),
    "Lead.Ingatlan_terhek__c": _entry("property.terhek", "Ingatlan terhek"),
    "Lead.Ingtalan_hazszam__c": _entry("property.address.house_number", "Ingatlan házszáma"),
    "Lead.PostalCode": _entry("participant.borrower.address.zip_code", "Irányítószám"),
    "Lead.Preferalt_futamido__c": _entry("deal.preferred_loan_term", "Preferált futamidő"),
    "Lead.Preferalt_kamatperiodus__c": _entry("deal.preferred_interest_period", "Preferált kamatperiódus"),
    "Lead.Purchase_price__c": _entry("property.purchase_price", "Ingatlan vételára"),
    "Lead.Related_Account__c": _entry("deal.account_id", "Kapcsolódó Account azonosító"),
    "Lead.State": _entry("participant.borrower.region", "Megye"),
    "Lead.Status": _entry("deal.lead_status", "Lead státusza"),
    "Lead.Street": _entry("participant.borrower.address.street", "Utca"),
    "Lead.Term_in_year__c": _entry("deal.loan_term_years", "Futamidő években"),
    "Lead.Tervezett_CSOK_Plusz__c": _entry("deal.csok_amount", "Tervezett CSOK Plusz összeg"),
    "Lead.Tervezett_Otthon_Start__c": _entry("deal.otthon_start_amount", "Tervezett Otthon Start összeg"),
    "Lead.Tervezett_onero__c": _entry("deal.down_payment", "Tervezett önerő"),
    "Lead.Tervezett_piaci_hitel__c": _entry("deal.loan_amount", "Tervezett piaci hitel összege"),
    # Account remains account-scoped; only its stable ID belongs to the deal.
    "Account.Id": _entry("deal.account_id", "Account azonosító"),
    "Account.Name": _entry("account.name", "Ügyfél neve"),
    "Account.AccountNumber": _entry("account.account_number", "Account száma"),
    "Account.Phone": _entry("account.phone", "Account telefonszáma"),
    "Account.Fax": _entry("account.fax", "Account faxa"),
    "Account.Website": _entry("account.website", "Account weboldala"),
    "Account.Industry": _entry("account.industry", "Account tevékenységi köre"),
    "Account.Description": _entry("account.description", "Account leírása"),
    "Account.BillingStreet": _entry("account.billing_street", "Számlázási utca"),
    "Account.BillingCity": _entry("account.billing_city", "Számlázási település"),
    "Account.BillingState": _entry("account.billing_state", "Számlázási megye"),
    "Account.BillingPostalCode": _entry("account.billing_postal_code", "Számlázási irányítószám"),
    "Account.BillingCountry": _entry("account.billing_country", "Számlázási ország"),
    "Account.ShippingStreet": _entry("account.shipping_street", "Szállítási utca"),
    "Account.ShippingCity": _entry("account.shipping_city", "Szállítási település"),
    "Account.ShippingPostalCode": _entry("account.shipping_postal_code", "Szállítási irányítószám"),
    "Account.ShippingCountry": _entry("account.shipping_country", "Szállítási ország"),
    # Opportunity and requested-loan fields.
    "Opportunity.AccountId": _entry("deal.account_id", "Account azonosító"),
    "Opportunity.Account__c": _entry("deal.referrer_name", "Tippadó neve"),
    "Opportunity.Amount": _entry("deal.loan_amount", "Hitelösszeg"),
    "Opportunity.Approve_in_the_Bank_Application_OTP__c": _entry("deal.bank_application_approved", "Banki igénylés jóváhagyása"),
    "Opportunity.Attended_the_call__c": _entry("deal.call_attended", "Részvétel a híváson"),
    "Opportunity.Bank_Approval__c": _entry("deal.bank_approval", "Banki jóváhagyás"),
    "Opportunity.Bank__c": _entry("deal.bank_name", "Potenciális bank"),
    "Opportunity.Banki_ugyintezo_uj_c__c": _entry("deal.bank_contact_name", "Banki ügyintéző neve"),
    "Opportunity.CampaignId": _entry("deal.campaign_id", "Kampány azonosító"),
    "Opportunity.Category__c": _entry("deal.category", "Kategória"),
    "Opportunity.Client_accepted_the_offer__c": _entry("deal.offer_accepted", "Ajánlat elfogadása"),
    "Opportunity.CloseDate": _entry("deal.close_date", "Lezárás dátuma"),
    "Opportunity.ContactId": _entry("participant.borrower.contact_id", "Contact azonosító"),
    "Opportunity.Contact_Name__c": _entry("participant.borrower.name", "Kontakt neve"),
    "Opportunity.Contract_signed_date__c": _entry("deal.contract_signed_date", "Szerződés aláírásának dátuma"),
    "Opportunity.Csabi_gylete__c": _entry("deal.csabi_deal", "Csabi ügylete"),
    "Opportunity.Date_when_bank_received_all_documents__c": _entry("deal.bank_documents_received_date", "Banki dokumentumátvétel dátuma"),
    "Opportunity.Description": _entry("deal.notes", "Ügylet megjegyzése"),
    "Opportunity.Fill_and_send_back_request_form__c": _entry("deal.form_return_requested", "Űrlap visszaküldése"),
    "Opportunity.First_Co_debtor__c": _entry("participant.co_borrower[1].contact_id", "Első adóstárs Contact azonosítója"),
    "Opportunity.Flag__c": _entry("deal.flag", "Jelölés"),
    "Opportunity.Hitel_sszeg__c": _entry("deal.loan_amount", "Hitelösszeg"),
    "Opportunity.Hitelc_l__c": _entry("deal.loan_purpose", "Hitel célja"),
    "Opportunity.Hitelez_bank__c": _entry("deal.bank_name", "Hitelező bank"),
    "Opportunity.Insurance__c": _entry("deal.insurance", "Biztosítás"),
    "Opportunity.Jutal_k_ideje__c": _entry("deal.commission_date", "Jutalék dátuma"),
    "Opportunity.Jutal_k_kifizetve__c": _entry("deal.commission_paid", "Jutalék kifizetése"),
    "Opportunity.Kamat__c": _entry("deal.interest_rate", "Kamatláb"),
    "Opportunity.LeadSource": _entry("deal.lead_source", "Lead forrása"),
    "Opportunity.Lead_Priority__c": _entry("deal.lead_priority", "Lead prioritása"),
    "Opportunity.Mortgagor__c": _entry("deal.has_mortgagor", "Zálogkötelezett jelenléte"),
    "Opportunity.Name": _entry("deal.name", "Ügylet neve"),
    "Opportunity.NextStep": _entry("deal.next_step", "Következő lépés"),
    "Opportunity.Opportunity_Contact_Name__c": _entry("participant.borrower.name", "Ügylet kontaktjának neve"),
    "Opportunity.Opportunity_ID__c": _entry("deal.deal_id", "Ügylet azonosító"),
    "Opportunity.OwnerId": _entry("deal.owner_id", "Ügylet tulajdonosának azonosítója"),
    "Opportunity.Pre_valuation_has_been_ordered__c": _entry("deal.pre_valuation_ordered", "Előzetes értékbecslés megrendelve"),
    "Opportunity.Pre_valuation_order_bank__c": _entry("deal.pre_valuation_bank", "Előzetes értékbecslés bankja"),
    "Opportunity.Pre_valuation_order_date__c": _entry("deal.pre_valuation_order_date", "Előzetes értékbecslés dátuma"),
    "Opportunity.Re_evaluate_the_quote__c": _entry("deal.quote_revaluation_requested", "Ajánlat újraértékelése"),
    "Opportunity.Second_Co_debtor__c": _entry("participant.co_borrower[2].contact_id", "Második adóstárs Contact azonosítója"),
    "Opportunity.Session_ID__c": _entry("deal.session_id", "Munkamenet azonosító"),
    "Opportunity.Sign_all_documents__c": _entry("deal.all_documents_signed", "Minden dokumentum aláírva"),
    "Opportunity.Sign_all_documents_needed__c": _entry("deal.document_signing_required", "Dokumentumok aláírása szükséges"),
    "Opportunity.StageName": _entry("deal.salesforce_stage", "Salesforce státusz"),
    "Opportunity.Term_k__c": _entry("deal.product_name", "Termék neve"),
    "Opportunity.Third_Co_debtor__c": _entry("participant.co_borrower[3].contact_id", "Harmadik adóstárs Contact azonosítója"),
    "Opportunity.Tippad_kifizetve__c": _entry("deal.referrer_paid", "Tippadó kifizetve"),
    "Opportunity.Tippado_uj__c": _entry("deal.referrer_name", "Tippadó neve"),
    "Opportunity.Type": _entry("deal.opportunity_type", "Ügylet típusa"),
    "Opportunity.Usufructuary__c": _entry("deal.has_usufructuary", "Haszonélvező jelenléte"),
    "Opportunity.Which_documents_are_missing__c": _entry("deal.missing_documents", "Hiányzó dokumentumok"),
    "Opportunity.bankiugyintezoneve__c": _entry("deal.bank_contact_name", "Banki ügyintéző neve"),
    "Opportunity.gylet_kezel_je__c": _entry("deal.deal_manager", "Ügyletkezelő"),
    "Opportunity.gylet_st_tusz__c": _entry("deal.salesforce_stage", "Ügylet státusza"),
    "Opportunity.remark__c": _entry("deal.notes", "Ügylet megjegyzése"),
    # Existing heuristic loan keys become canonical deal paths offline.
    "loan.loan_amount": _entry("deal.loan_amount", "Hitelösszeg"),
    "loan.loan_purpose": _entry("deal.loan_purpose", "Hitel célja"),
    "loan.monthly_payment": _entry("deal.monthly_payment", "Becsült havi törlesztő"),
    "loan.down_payment": _entry("deal.down_payment", "Önerő"),
    "loan.purchase_price": _entry("deal.purchase_price", "Vételár"),
    "loan.csok_amount": _entry("deal.csok_amount", "CSOK támogatás összege"),
    "loan.afa_support": _entry("deal.afa_support", "ÁFA támogatás összege"),
    "loan.housing_savings": _entry("deal.housing_savings", "Lakástakarék összege"),
    "loan.product_type": _entry("deal.product_type", "Termék típusa"),
    "loan.refinance_account": _entry("deal.refinance_account", "Kiváltandó hitel számlaszáma"),
    "participant.role": _entry("participant.borrower.role", "Szerep"),
    # New org-audit concepts absent from the old static catalog.
    "Contact.Nem__c": _entry("participant.borrower.gender", "Nem"),
    "Contact.Personal_Number__c": _entry("participant.borrower.personal_number", "Személyi szám"),
    "Contact.ID_Document_Type__c": _entry("participant.borrower.id_document_type", "Okmány típusa"),
    "Contact.Document_Type__c": _entry("participant.borrower.id_document_type", "Okmány típusa"),
    "Contact.Household_Size__c": _entry("participant.borrower.household_size", "Háztartás létszáma"),
    "Contact.Children_Count__c": _entry("participant.borrower.children_count", "Gyermekek száma"),
    "Contact.KHR_Consent__c": _entry("participant.borrower.khr_consent", "KHR hozzájárulás"),
    "Contact.NAV_Consent__c": _entry("participant.borrower.nav_consent", "NAV hozzájárulás"),
    "Contact.AML_Consent__c": _entry("participant.borrower.aml_consent", "AML hozzájárulás"),
    "Contact.Data_Processing_Consent__c": _entry("participant.borrower.data_processing_consent", "Adatkezelési hozzájárulás"),
    "Contact.Employer_Tax_Number__c": _entry("participant.borrower.employer_tax_number", "Munkáltató adószáma"),
    "Contact.Employer_Company_Form__c": _entry("participant.borrower.employer_company_form", "Munkáltató cégformája"),
    "Contact.Credit_Commitment__c": _entry("participant.borrower.credit_commitment", "Hitelvállalás"),
    "Lead.Ingatlan_allapot__c": _entry("property.condition", "Ingatlan állapota"),
    "Lead.Ingatlan_futes__c": _entry("property.heating", "Ingatlan fűtése"),
    "Lead.Ingatlan_epites_eve__c": _entry("property.year_built", "Építés éve"),
    "Lead.Ingatlan_lift__c": _entry("property.lift", "Lift megléte"),
    "Lead.Ingatlan_biztositas__c": _entry("property.insurance", "Ingatlanbiztosítás"),
    "Opportunity.Discount_Action__c": _entry("deal.discount_action", "Kedvezményakció"),
    "Opportunity.Kedvezmeny_akcio__c": _entry("deal.discount_action", "Kedvezményakció"),
    "Opportunity.Refinance_Account__c": _entry("deal.refinance_account", "Kiváltandó hitel számlaszáma"),
    # Former recognizer-only audit names are also migration inputs, not aliases.
    "participant.business_name": _entry("participant.borrower.employer", "Vállalkozás neve"),
    "participant.employer_tax_id": _entry("participant.borrower.employer_tax_number", "Munkáltató adószáma"),
    "participant.business_tax_id": _entry("participant.borrower.employer_tax_number", "Vállalkozás adószáma"),
}

_FRAGMENT_SUFFIXES = {"ev": "year", "ho": "month", "nap": "day"}
_PRODUCT_GATES = frozenset({"piaci", "tamogatott"})
_GATE_TARGETS = frozenset({
    "deal.loan_amount",
    "deal.loan_purpose",
    "deal.loan_term_years",
    "deal.insurance",
    "property.insurance",
})
_PROPERTY_INDEX_RE = re.compile(r"^(?P<base>.+)_(?P<index>[1-9])$")


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value or "")
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _count_target(label: str, *, required: bool) -> str | None:
    folded = _fold(label).strip()
    categories = []
    if "haztartas" in folded and "letszam" in folded:
        categories.append("household_size")
    if "eltartott" in folded:
        categories.append("dependents_count")
    if "gyermek" in folded:
        categories.append("children_count")
    if len(categories) > 1:
        raise ValueError(f"Ambiguous count label: {label!r}")
    if not categories:
        if required:
            raise ValueError(f"Count label must identify children, dependents, or household: {label!r}")
        return None
    return categories[0]


def _base_and_suffix(path: str) -> tuple[str, str, str | None]:
    if not isinstance(path, str) or not path:
        raise ValueError("Legacy path must be a non-empty string")
    base = path
    role = "borrower"
    if base.endswith("-társ"):
        base = base.removesuffix("-társ")
        role = "co_borrower"
    suffix = None
    if "#" in base:
        base, suffix = base.rsplit("#", 1)
        if not base or not suffix or "#" in base:
            raise ValueError(f"Malformed legacy suffix: {path!r}")
    return base, role, suffix


def _label_for_legacy(path: str) -> str:
    try:
        base, _, _ = _base_and_suffix(path)
    except ValueError:
        return ""
    indexed = _PROPERTY_INDEX_RE.fullmatch(base)
    if indexed and indexed["base"] in _LEGACY_FIELDS:
        base = indexed["base"]
    entry = _LEGACY_FIELDS.get(base)
    return entry.label if entry else ""


def _dynamic_base_translation(base: str, label: str, pdf_name: str) -> Translation:
    translation = _LEGACY_FIELDS[base].translation
    folded_label = _fold(label)
    folded_pdf = _fold(pdf_name)

    if base == "Contact.Name" and (
        "jogi szemely" in folded_label or "cegnev" in folded_label
    ):
        return Translation("account.name")
    if base == "Contact.OtherStreet" and "szekhely" in folded_label and (
        "jogi szemely" in folded_label or "vallalkozas" in folded_label
    ):
        return Translation("account.billing_street")
    if base == "Contact.Phone" and "jogi szemely" in folded_label:
        return Translation("account.phone")
    if base == "Contact.Salutation" and (
        re.search(r"\bneme\b", folded_label) or re.search(r"\bnem\s*[:–-]", folded_label)
    ):
        return Translation("participant.borrower.gender")
    if base == "Contact.Dependents_count__c":
        count_target = _count_target(label, required=bool(folded_label))
        if count_target:
            return Translation(f"participant.borrower.{count_target}")
    elif base == "Contact.Numer_and_Age_of_Children__c":
        count_target = _count_target(label, required=bool(folded_label))
        if count_target == "children_count" and "eletkor" in folded_label:
            return Translation("participant.borrower.children_details")
        if count_target:
            return Translation(f"participant.borrower.{count_target}")
    elif base == "Opportunity.Insurance__c":
        if "vagyonbiztos" in folded_label or "lakasbiztos" in folded_label:
            return Translation("property.insurance")
    elif base == "Contact.State_Support__c":
        if re.search(r"\bafa\b", folded_label) or "afa" in folded_pdf:
            return Translation("deal.afa_support")
    elif base == "Contact.Monthly_installment_payment__c":
        if "becsult" in folded_label or folded_pdf == "loan_monthly_payment":
            return Translation("deal.monthly_payment")
    elif base == "Lead.State" and "ingatlan" in folded_label:
        return Translation("property.region")
    elif base == "Contact.Description":
        if "nav" in folded_label or "kereset-kimutatas" in folded_label:
            return Translation("participant.borrower.nav_consent")
        if "adatkezel" in folded_label:
            return Translation("participant.borrower.data_processing_consent")
    return translation


def _with_role(translation: Translation, role: str) -> Translation:
    if role == "borrower":
        return translation
    prefix = "participant.borrower"
    if translation.path == prefix or not translation.path.startswith(prefix + "."):
        raise ValueError(f"Co-borrower suffix requires a participant path: {translation.path!r}")
    return Translation(
        path="participant.co_borrower" + translation.path[len(prefix):],
        fragment=translation.fragment,
        product_gate=translation.product_gate,
    )


def translate_legacy_path(
    path: str, *, label: str = "", pdf_field_name: str = ""
) -> Translation:
    """Translate one legacy entry or raise ``ValueError`` when it is unknown."""

    base, role, suffix = _base_and_suffix(path)
    pdf_co_borrower = "-társ" in (pdf_field_name or "")

    index = None
    if base not in _LEGACY_FIELDS:
        indexed = _PROPERTY_INDEX_RE.fullmatch(base)
        if indexed and indexed["base"] in _LEGACY_FIELDS:
            base = indexed["base"]
            index = int(indexed["index"])

    if base not in _LEGACY_FIELDS:
        raise ValueError(f"Unresolved legacy path: {path!r}")
    translation = _dynamic_base_translation(base, label, pdf_field_name)

    if index is not None:
        if not translation.path.startswith("property."):
            raise ValueError(f"Indexed legacy path is not property-scoped: {path!r}")
        translation = Translation(
            path="property[" + str(index) + "]" + translation.path[len("property"):],
            fragment=translation.fragment,
            product_gate=translation.product_gate,
        )

    if suffix is not None:
        if suffix in _FRAGMENT_SUFFIXES:
            if translation.path.startswith("property.") or parse_path(translation.path).field not in _DATE_FIELDS:
                raise ValueError(f"Date fragment on a non-date legacy path: {path!r}")
            translation = Translation(translation.path, fragment=_FRAGMENT_SUFFIXES[suffix])
        elif suffix in _PRODUCT_GATES:
            if translation.path not in _GATE_TARGETS:
                raise ValueError(f"Product gate on an unsupported legacy path: {path!r}")
            translation = Translation(
                translation.path,
                fragment=translation.fragment,
                product_gate=suffix,
            )
        elif suffix == "count" and base == "Contact.Numer_and_Age_of_Children__c":
            target = _count_target(label, required=True)
            translation = Translation(f"participant.borrower.{target}")
        else:
            raise ValueError(f"Unknown legacy suffix in {path!r}")

    if (
        role == "borrower"
        and pdf_co_borrower
        and translation.path.startswith("participant.borrower.")
    ):
        role = "co_borrower"
    translation = _with_role(translation, role)
    parse_path(translation.path)
    return translation


_PARTICIPANT_DIRECT = frozenset({
    "name", "contact_id", "birth_name", "mother_name", "birth_place", "birth_date",
    "personal_id", "personal_number", "tax_id", "id_card_number", "id_document_type",
    "phone", "email", "employer", "employer_tax_number", "employer_company_form",
    "monthly_income", "income_bearing", "marital_status", "citizenship", "gender",
    "household_size", "dependents_count", "children_count", "credit_commitment",
    "education", "income_type", "is_active", "salutation", "occupation",
    "foglalkozas_tipusa", "employment_type", "relation", "legal_title",
    "jovairas_vallalasa", "loan_purpose", "interest_period", "other_monthly_income",
    "current_debt_credit_limit", "date_of_notification_for_residence", "date_of_signature",
    "description", "mailing_city", "mailing_postal_code", "mailing_street", "other_city",
    "other_street", "other_country", "mobile_phone", "account_holding_bank",
    "cafeteria_bonus", "continuous_tb_2_years", "contract_end_date", "contract_start_date",
    "date_of_handover", "date_of_transfer", "description_of_loan_purpose", "divident",
    "employee_registration", "employer_company_type", "income_from_self_owned", "industry",
    "monthly_payment_details", "monthly_installment_payment", "mortgagor",
    "numer_and_age_of_children", "other_income", "other_monthly_deductions", "own_resources",
    "property_50pct_ownership_details", "property_encumbrances", "property_value",
    "self_employment_details", "state_support", "town_or_possibly_exact_address",
    "usufructuary", "what_type_of_loan", "contact_loan_amount", "contact_loan_period",
    "current_employment_started", "ownership_in_employer", "preferalt_bank",
    "residence_status", "role",
})
_PROPERTY_DIRECT = frozenset({
    "parcel_number", "area_sqm", "property_type", "property_type_detail", "condition",
    "heating", "lift", "insurance", "estimated_value", "year_built", "number_of_rooms",
    "purchase_price", "form_type", "energetika", "terhek", "ingatlan_szerepe", "epites_eve",
})
_LOAN_DIRECT = frozenset({
    "loan_amount", "loan_term_months", "loan_term_years", "interest_period", "loan_purpose",
    "product_name", "product_type", "down_payment", "monthly_payment", "purchase_price",
    "csok_amount", "afa_support", "housing_savings", "refinance_account", "discount_action",
})
_DEAL_DIRECT = frozenset({
    "deal_id", "salesforce_stage", "created_at", "notes", "account_id",
    "has_mortgagor", "has_usufructuary",
})
_DATE_FIELDS = frozenset({
    "birth_date", "email_bounced_date", "contract_end_date", "contract_start_date",
    "current_employment_started", "date_of_handover", "residence_notification_date",
    "date_of_signature", "date_of_transfer", "close_date", "contract_signed_date",
    "bank_documents_received_date", "commission_date", "pre_valuation_order_date", "created_at",
})
_MONEY_FIELDS = frozenset({
    "loan_amount", "down_payment", "monthly_payment", "purchase_price", "csok_amount",
    "afa_support", "housing_savings", "annual_revenue", "estimated_value", "affordable_monthly_installments",
    "monthly_income", "cafeteria_bonus", "divident", "income_from_self_owned", "other_income",
    "other_monthly_deductions", "other_monthly_income", "repayment_fee", "secondary_loan_amount",
    "tertiary_loan_amount", "secondary_affordable_monthly_installments", "tertiary_affordable_monthly_installments",
    "otthon_start_amount", "original_amount", "outstanding_amount", "amount",
})
_INTEGER_FIELDS = frozenset({
    "household_size", "dependents_count", "children_count", "loan_term_months", "year_built",
    "number_of_rooms", "sequence",
})
_NUMBER_FIELDS = frozenset({
    "area_sqm", "interest_rate", "referral_fee_percentage", "loan_term_years",
    "secondary_loan_term_years", "tertiary_loan_term_years",
})
_BOOLEAN_FIELDS = frozenset({
    "email_opt_out", "is_email_bounced", "is_priority_record", "has_mortgagor", "has_usufructuary",
    "recent_loan_application_90_days", "bank_application_approved", "call_attended", "bank_approval",
    "offer_accepted", "csabi_deal", "form_return_requested", "commission_paid", "pre_valuation_ordered",
    "quote_revaluation_requested", "all_documents_signed", "document_signing_required", "referrer_paid",
    "lift", "refinance", "income_bearing",
})

_CANONICAL_LABELS = {
    "deal.loan_amount": "Hitelösszeg",
    "deal.account_id": "Account azonosító",
    "deal.down_payment": "Önerő",
    "deal.purchase_price": "Vételár",
    "deal.loan_term_years": "Futamidő években",
    "deal.loan_purpose": "Hitel célja",
    "deal.product_type": "Termék típusa",
    "deal.monthly_payment": "Becsült havi törlesztő",
    "deal.notes": "Megjegyzések",
    "deal.salesforce_stage": "Salesforce státusz",
    "deal.lead_source": "Lead forrása",
    "deal.lead_priority": "Lead prioritása",
    "deal.next_step": "Következő lépés",
    "deal.missing_documents": "Hiányzó dokumentumok",
    "deal.session_id": "Munkamenet azonosító",
    "deal.bank_name": "Bank",
    "deal.bank_contact_name": "Banki ügyintéző neve",
    "deal.referrer_name": "Tippadó neve",
    "deal.has_mortgagor": "Zálogkötelezett jelenléte",
    "deal.has_usufructuary": "Haszonélvező jelenléte",
    "deal.afa_support": "ÁFA támogatás összege",
    "deal.csok_amount": "CSOK támogatás összege",
    "deal.refinance_account": "Kiváltandó hitel számlaszáma",
    "deal.discount_action": "Kedvezményakció",
    "account.name": "Ügyfél neve",
    "account.phone": "Account telefonszáma",
    "account.billing_street": "Számlázási utca",
    "participant.{role}.name": "Teljes név",
    "participant.{role}.contact_id": "Contact azonosító",
    "participant.{role}.birth_date": "Születési dátum",
    "participant.{role}.gender": "Nem",
    "participant.{role}.personal_number": "Személyi szám",
    "participant.{role}.id_document_type": "Okmány típusa",
    "participant.{role}.employer": "Munkáltató neve",
    "participant.{role}.employer_tax_number": "Munkáltató adószáma",
    "participant.{role}.employer_company_form": "Munkáltató cégformája",
    "participant.{role}.employee_registration": "Munkavállalói regisztráció",
    "participant.{role}.employment_type": "Munkaviszony típusa",
    "participant.{role}.industry": "Iparág",
    "participant.{role}.household_size": "Háztartás létszáma",
    "participant.{role}.dependents_count": "Eltartottak száma",
    "participant.{role}.children_count": "Gyermekek száma",
    "participant.{role}.children_details": "Gyermekek száma és életkora",
    "participant.{role}.residence_notification_date": "Lakcím bejelentésének dátuma",
    "participant.{role}.address.city": "Állandó lakcím – település",
    "participant.{role}.address.zip_code": "Állandó lakcím – irányítószám",
    "participant.{role}.address.street": "Állandó lakcím – utca",
    "participant.{role}.role": "Szerep",
    "participant.{role}.khr_consent": "KHR hozzájárulás",
    "participant.{role}.nav_consent": "NAV hozzájárulás",
    "participant.{role}.aml_consent": "AML hozzájárulás",
    "participant.{role}.data_processing_consent": "Adatkezelési hozzájárulás",
    "property.parcel_number": "Helyrajzi szám",
    "property.address.city": "Ingatlan település",
    "property.address.zip_code": "Ingatlan irányítószáma",
    "property.address.house_number": "Ingatlan házszáma",
    "property.estimated_value": "Becsült piaci érték",
    "property.property_type": "Ingatlan típusa",
    "property.terhek": "Ingatlan terhek",
    "property.region": "Ingatlan megyéje",
    "property.insurance": "Ingatlanbiztosítás",
}
_SOURCE_OVERRIDES = {
    "participant.{role}.residence_notification_date": "participant.date_of_notification_for_residence",
    "participant.{role}.children_details": "participant.numer_and_age_of_children",
    "participant.{role}.permanent_address": "participant.address.full_address",
    "participant.{role}.khr_consent": "consents.khr",
    "participant.{role}.nav_consent": "consents.nav",
    "participant.{role}.aml_consent": "consents.aml",
    "participant.{role}.data_processing_consent": "consents.data_processing",
}


def _template_for(path: str) -> tuple[str, str]:
    parsed = parse_path(path)
    if parsed.namespace == "participant":
        if parsed.collection:
            return f"participant.{{role}}.{parsed.collection}.{parsed.field}", parsed.collection
        return f"participant.{{role}}.{parsed.field}", "participant"
    if parsed.namespace == "property":
        return f"property.{parsed.field}", "property"
    return path, "none"


def _source_for(template: str, path: str) -> str:
    if template in _SOURCE_OVERRIDES:
        return _SOURCE_OVERRIDES[template]
    parsed = parse_path(path)
    field = parsed.field
    if parsed.collection:
        return f"{parsed.collection}.{field}"
    if parsed.namespace == "participant":
        if field.startswith(("address.", "mailing_address.")):
            return f"participant.{field}"
        if field in _PARTICIPANT_DIRECT:
            return f"participant.{field}"
        return f"extra.{field}"
    if parsed.namespace == "property":
        if field.startswith("address."):
            return f"property.{field}"
        if field in _PROPERTY_DIRECT:
            return f"property.{field}"
        return f"extra.{field}"
    if parsed.namespace == "account":
        return f"account.{field}"
    if field in _LOAN_DIRECT:
        return f"loan.{field}"
    if field in _DEAL_DIRECT:
        return f"deal.{field}"
    return f"extra.{field}"


def _type_for(field: str) -> str:
    if field in _DATE_FIELDS:
        return "date"
    if field in _MONEY_FIELDS:
        return "money"
    if field in _INTEGER_FIELDS:
        return "integer"
    if field in _NUMBER_FIELDS:
        return "number"
    if field in _BOOLEAN_FIELDS or field.endswith("_consent"):
        return "boolean"
    return "string"


def definition_for_translation(
    legacy_path: str, translation: Translation, label: str = ""
) -> dict:
    """Return deterministic registry metadata for one translated legacy path."""

    template, repeat = _template_for(translation.path)
    field = parse_path(translation.path).field
    canonical_label = (
        _CANONICAL_LABELS.get(template)
        or _label_for_legacy(legacy_path)
        or label.strip()
        or template
    )
    return {
        "path": template,
        "label": canonical_label,
        "type": _type_for(field),
        "source": _source_for(template, translation.path),
        "repeat": repeat,
    }


def explicit_legacy_fields() -> dict[str, str]:
    """Return the explicit base inventory used to supplement old mappings."""

    return {path: entry.label for path, entry in _LEGACY_FIELDS.items()}
