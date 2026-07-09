import re

old_to_new = {
    "participant.name": "Contact.Name",
    "participant.birth_name": "Contact.Szuletesi_nev__c",
    "participant.mother_name": "Contact.Mother_s_Name__c",
    "participant.birth_place": "Contact.Place_of_Birth__c",
    "participant.birth_date": "Contact.Date_of_birth__c",
    "participant.personal_id": "Contact.ID_Card_Number__c",
    "participant.id_card_number": "Contact.ID_Card_Number__c",
    "participant.tax_id": "Contact.Tax_ID__c",
    
    "participant.address.full_address": "Contact.Permanent_address__c",
    "participant.address.zip_code": "Contact.ZIP__c",
    "participant.address.city": "Contact.MailingCity",
    "participant.address.street": "Contact.MailingStreet",
    "participant.address.house_number": "Contact.MailingStreet",
    "participant.address.country": "Contact.MailingCountry",

    "participant.mailing_address.zip_code": "Contact.MailingPostalCode",
    "participant.mailing_address.city": "Contact.MailingCity",
    "participant.mailing_address.street": "Contact.MailingStreet",
    "participant.mailing_address_same": "Contact.MailingStreet",
    
    "participant.phone": "Contact.Phone",
    "participant.email": "Contact.Email",
    "participant.dependents": "Contact.Dependents_count__c",
    "participant.kata_status": "Contact.Self_employment_details__c",
    "participant.employee_count": "Contact.Self_employment_details__c",
    "participant.residence_since": "Contact.Date_of_notification_for_residence__c",
    "participant.role": "Contact.Relation__c",
    "participant.gender": "Contact.Salutation",
    "participant.citizenship": "Contact.Citizenship__c",
    "participant.marital_status": "Contact.Marital_Status__c",
    "participant.id_document_type": "Contact.ID_Card_Number__c",
    "participant.education": "Contact.Highest_Educational_Qualification__c",
    "participant.employment_type": "Contact.Employment_Type_c__c",
    "participant.nav_declaration": "Contact.Description",

    "participant.employer": "Contact.Name_of_employer__c",
    "participant.monthly_income": "Contact.Average_monthly_net_income__c",

    "loan.loan_amount": "Opportunity.Hitel_sszeg__c",
    "loan.loan_term_months": "Contact.Term_in_year_c__c",
    "loan.interest_period": "Contact.Interest_Period__c",
    "loan.loan_purpose": "Contact.Loan_Purpose__c",
    "loan.down_payment": "Lead.Tervezett_onero__c",
    "loan.monthly_payment": "Contact.Affordable_monthly_installments__c",
    "loan.purchase_price": "Lead.Purchase_price__c",
    "loan.csok_amount": "Lead.Tervezett_CSOK_Plusz__c",
    "loan.afa_support": "Contact.State_Support__c",

    "property.parcel_number": "Lead.Ingatlan_jellege__c",
    "property.area_sqm": "Lead.Ingatlan_alapterulet__c",
    "property.property_type": "Lead.Ingatlan_jellege__c",
    "property.estimated_value": "Lead.Estimated__c",
    "property.usage_type": "Lead.Ingatlan_szerepe__c",
    "property.rental_fee": "Contact.Other_income__c",
    "property.rental_fee_eur": "Contact.Other_income__c",
    "property.contact_name": "Opportunity.Contact_Name__c",
    "property.contact_phone": "Contact.OtherPhone",
}

path = "src/ai/field_recognizer.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Replace dict values inside KEYWORD_MAP and OTP_EXACT_MAP
for old_val, new_val in old_to_new.items():
    content = re.sub(rf'"{old_val}"', f'"{new_val}"', content)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("Replacement done.")
