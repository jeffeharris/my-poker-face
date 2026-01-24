import { useNavigate } from 'react-router-dom';
import { PageLayout } from '../shared/PageLayout';
import { BackButton } from '../shared/BackButton';
import './LegalPage.css';

export function TermsOfService() {
  const navigate = useNavigate();

  return (
    <PageLayout variant="top" glowColor="sapphire" maxWidth="md">
      <BackButton onClick={() => navigate(-1)} />
      <div className="legal-page">
        <h1>Terms of Service</h1>
        <p className="legal-page__effective">Effective Date: January 24, 2026</p>

        <section>
          <h2>Agreement to Terms</h2>
          <p>
            By accessing or using My Poker Face, you agree to be bound by these Terms of Service.
            If you do not agree to these terms, please do not use the application.
          </p>
        </section>

        <section>
          <h2>Description of Service</h2>
          <p>
            My Poker Face is a poker game application featuring AI-powered opponents. The game
            is for entertainment purposes only and does not involve real money gambling.
          </p>
        </section>

        <section>
          <h2>User Accounts</h2>
          <p>
            You may sign in using your Google account. You are responsible for maintaining the
            security of your account and for all activities that occur under your account.
          </p>
        </section>

        <section>
          <h2>Acceptable Use</h2>
          <p>You agree not to:</p>
          <ul>
            <li>Use the service for any unlawful purpose</li>
            <li>Attempt to exploit, hack, or disrupt the service</li>
            <li>Use automated tools or bots to interact with the service</li>
            <li>Abuse or harass other users</li>
          </ul>
        </section>

        <section>
          <h2>Intellectual Property</h2>
          <p>
            The application and its content are owned by Jeff Harris. You may not copy, modify,
            or distribute the application without permission.
          </p>
        </section>

        <section>
          <h2>Disclaimer</h2>
          <p>
            The service is provided "as is" without warranties of any kind. We do not guarantee
            that the service will be uninterrupted, secure, or error-free.
          </p>
        </section>

        <section>
          <h2>Limitation of Liability</h2>
          <p>
            To the fullest extent permitted by law, Jeff Harris shall not be liable for any
            indirect, incidental, special, or consequential damages arising from your use of
            the service.
          </p>
        </section>

        <section>
          <h2>Termination</h2>
          <p>
            We reserve the right to suspend or terminate your access to the service at any time,
            for any reason, without notice.
          </p>
        </section>

        <section>
          <h2>Changes to Terms</h2>
          <p>
            We may modify these terms at any time. Continued use of the service after changes
            constitutes acceptance of the new terms.
          </p>
        </section>

        <section>
          <h2>Contact</h2>
          <p>
            For questions about these Terms, contact us at:{' '}
            <a href="mailto:jeff.harris@jeffharrisconsulting.com">jeff.harris@jeffharrisconsulting.com</a>
          </p>
        </section>
      </div>
    </PageLayout>
  );
}
